"""FastAPI Web 应用：REST API + WebSocket + 静态前端托管。

端点:
- ``GET  /api/health``            健康检查
- ``GET  /api/sessions``          会话列表
- ``POST /api/sessions``          新建会话
- ``GET  /api/sessions/{id}/messages``  会话历史
- ``WS   /ws/{id}``               对话（流式事件）

认证：设置环境变量 ``AGENT_WEB_TOKEN`` 后，所有端点要求携带 token
（REST 用 ``Authorization: Bearer <token>`` 或 ``?token=``，WS 用 ``?token=``）。
未设置时不鉴权（仅限互信网络）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent_shell import __version__
from agent_shell.config import Settings, load_settings
from agent_shell.errors import SessionError
from agent_shell.server.events import ClientMessage
from agent_shell.server.manager import SessionManager

_DIST_DIR = Path(__file__).resolve().parents[2] / "webui" / "dist"

_TOKEN_UNSET = object()


class RenameRequest(BaseModel):
    """重命名会话请求体。

    Attributes:
        title: 新标题（1..64 字符）。
    """

    title: str = Field(min_length=1, max_length=64)


def _find_dist_dir() -> Path | None:
    """定位前端构建产物目录（webui/dist）。

    Returns:
        dist 目录；不存在返回 None（仅提供 API）。
    """
    dist = _DIST_DIR
    return dist if dist.is_dir() else None


def _auth_ok(configured: str | None, provided: str | None) -> bool:
    """校验 token。

    Args:
        configured: 配置的 token（None 表示不鉴权）。
        provided: 请求携带的 token。

    Returns:
        是否放行。
    """
    if not configured:
        return True
    return bool(provided) and provided == configured


def _require_auth(
    authorization: Annotated[str | None, Header()] = None,
    token: Annotated[str | None, Query()] = None,
    api_token: str | None = None,
) -> None:
    """REST 依赖：未携带有效 token 时抛 401。

    Args:
        authorization: Authorization 头。
        token: query 参数。
        api_token: 配置的 token（闭包注入）。

    Raises:
        HTTPException: 鉴权失败。
    """
    provided: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    provided = provided or token
    if not _auth_ok(api_token, provided):
        raise HTTPException(status_code=401, detail="无效或缺失的访问令牌")


def create_app(
    settings: Settings | None = None,
    manager: SessionManager | None = None,
    *,
    api_token: str | None | object = _TOKEN_UNSET,
) -> FastAPI:
    """创建 FastAPI 应用。

    Args:
        settings: 全局配置；None 时从环境/配置文件加载。
        manager: 会话管理器；None 时基于 settings 构建（测试可注入替身）。
        api_token: 访问令牌；不传时读取环境变量 ``AGENT_WEB_TOKEN``，
            传 None 表示显式禁用鉴权（避免测试被 .env 隐式加载污染）。

    Returns:
        FastAPI 实例。
    """
    settings = settings or load_settings()
    manager = manager or SessionManager(settings)
    if api_token is _TOKEN_UNSET:
        api_token = os.environ.get("AGENT_WEB_TOKEN")

    app = FastAPI(
        title="果冻",
        version=__version__,
        description="类 Claude Code 的终端 Agent · Web 界面",
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """健康检查。"""
        return {"status": "ok", "version": __version__, "model": settings.model}

    @app.get("/api/sessions", dependencies=[Depends(_require_auth_closure(api_token))])
    def list_sessions() -> dict[str, Any]:
        """列出全部会话。"""
        return {"sessions": manager.list_sessions()}

    @app.post("/api/sessions", dependencies=[Depends(_require_auth_closure(api_token))])
    def create_session() -> dict[str, str]:
        """新建会话。"""
        session = manager.create_session()
        return {"session_id": session.session_id}

    @app.patch(
        "/api/sessions/{session_id}",
        dependencies=[Depends(_require_auth_closure(api_token))],
    )
    def rename_session(session_id: str, body: RenameRequest) -> dict[str, str]:
        """重命名会话（会话不存在 404，标题非法 400）。"""
        try:
            manager.get_session(session_id)
        except SessionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            session = manager.rename_session(session_id, body.title)
        except SessionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"session_id": session.session_id, "title": session.title}

    @app.delete(
        "/api/sessions/{session_id}",
        dependencies=[Depends(_require_auth_closure(api_token))],
    )
    def delete_session(session_id: str) -> dict[str, str]:
        """删除会话。"""
        try:
            manager.delete_session(session_id)
        except SessionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": session_id}

    @app.get(
        "/api/sessions/{session_id}/messages",
        dependencies=[Depends(_require_auth_closure(api_token))],
    )
    def session_messages(session_id: str) -> dict[str, Any]:
        """获取会话历史消息。"""
        try:
            session = manager.get_session(session_id)
        except SessionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"messages": manager.serialize_messages(session)}

    @app.websocket("/ws/{session_id}")
    async def chat_websocket(websocket: WebSocket, session_id: str) -> None:
        """WebSocket 对话通道：接收用户消息，流式返回事件。"""
        if not _auth_ok(api_token, websocket.query_params.get("token")):
            await websocket.close(code=1008, reason="无效或缺失的访问令牌")
            return
        await websocket.accept()
        lock = manager.lock(session_id)
        try:
            while True:
                raw = await websocket.receive_json()
                message = ClientMessage.model_validate(raw)
                async with lock:
                    await manager.run_agent(
                        session_id,
                        message.content,
                        lambda event: websocket.send_json(event.model_dump()),
                    )
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001 - 连接级兜底，记录后断开
            await websocket.close(code=1011, reason=f"{type(exc).__name__}: {exc}")

    @app.exception_handler(SessionError)
    async def session_error_handler(_request: Any, exc: SessionError) -> JSONResponse:
        """会话错误统一映射为 404。"""
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    dist = _find_dist_dir()
    if dist is not None:
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="webui")
    else:
        @app.get("/")
        def index() -> dict[str, str]:
            """前端未构建时的提示。"""
            return {
                "message": "前端未构建。请在 webui/ 目录执行 npm install && npm run build",
            }

    return app


def _require_auth_closure(api_token: str | None) -> Any:
    """构造带配置 token 的鉴权依赖。

    Args:
        api_token: 配置的 token。

    Returns:
        可直接用于 Depends 的依赖函数。
    """
    def dependency(
        authorization: Annotated[str | None, Header()] = None,
        token: Annotated[str | None, Query()] = None,
    ) -> None:
        _require_auth(authorization, token, api_token)

    return dependency


app = create_app()
