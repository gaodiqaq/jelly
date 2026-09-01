"""FastAPI Web 应用：REST API + WebSocket + 静态前端托管。

端点:
- ``GET  /api/health``            健康检查
- ``GET  /api/config``            读取运行时配置（模型/提供商，Key 掩码）
- ``PUT  /api/config``            更新运行时配置（热生效并持久化）
- ``POST /api/config/test``       测试模型连通性
- ``GET  /api/sessions``          会话列表
- ``POST /api/sessions``          新建会话
- ``GET  /api/sessions/{id}/messages``  会话历史
- ``GET  /api/permissions``       读取权限模式
- ``PUT  /api/permissions``       切换权限模式（readonly/ask/auto/deny）
- ``GET  /api/files``             工作区目录列表（工作台文件树）
- ``GET  /api/file``              工作区文本文件内容（预览）
- ``GET  /api/file/raw``          工作区文件原始字节（图片/下载）
- ``WS   /ws/{id}``               对话（流式事件；支持 ``{"type":"stop"}`` 停止、
                                  ``{"type":"approval_decision"}`` 权限决策）

多用户: 环境变量 ``AGENT_WEB_USERS="alice:token1,bob:token2"`` 定义用户与口令，
每个用户拥有独立的会话与待办目录（``~/.agent_shell/users/<user>/sessions``），
聊天记录互不可见。仅设置 ``AGENT_WEB_TOKEN`` 时退化为单用户（无隔离，目录不变）。
未设置任何口令时不鉴权（仅限互信网络）。
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
import threading
import time
from pathlib import Path
from typing import Annotated, Any

import litellm
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent_shell import __version__
from agent_shell.config import Settings, load_settings
from agent_shell.errors import SessionError
from agent_shell.llm.client import LLMClient, is_private_base, proxy_bypass_client
from agent_shell.runtime import ProviderStore
from agent_shell.server.events import ClientMessage
from agent_shell.server.manager import SessionManager

_DIST_DIR = Path(__file__).resolve().parents[2] / "webui" / "dist"

_TOKEN_UNSET = object()

_USER_DIR_TMPL = Path.home() / ".agent_shell" / "users"

# 工作台文件预览的最大字符数（超出截断）
_MAX_PREVIEW_CHARS = 200_000


def _workspace_path(root: Path, rel: str) -> Path:
    """把工作区相对路径安全解析为绝对路径（拒绝越出工作区）。

    相对路径基于工作区根解析；绝对路径仅当位于工作区内时允许
    （agent 回复中常出现完整路径，需可点击预览）。

    Args:
        root: 工作区根目录（settings.cwd）。
        rel: 用户提供的路径。

    Returns:
        规范化后的绝对路径。

    Raises:
        HTTPException: 路径越出工作区（400）。
    """
    rel = (rel or ".").strip() or "."
    candidate = Path(rel).expanduser()
    try:
        resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"路径非法: {exc}") from exc
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise HTTPException(status_code=400, detail="路径超出工作区范围")
    return resolved


class RenameRequest(BaseModel):
    """重命名会话请求体。

    Attributes:
        title: 新标题（1..64 字符）。
    """

    title: str = Field(min_length=1, max_length=64)


class ConfigUpdate(BaseModel):
    """更新运行时配置请求体。

    Attributes:
        provider: 目标提供商名（如 ``openai``）。
        model: 切换当前模型（litellm 格式，含提供商前缀）。
        api_key: 提供商新 API Key；None 表示不修改。
        api_base: 提供商新 Base URL；None 表示不修改。
        cwd: 切换全局工作目录（如 ``D:/work/project``）。
    """

    provider: str = Field(min_length=1, max_length=64)
    model: str | None = None
    api_key: str | None = None
    api_base: str | None = None
    cwd: str | None = None


class ConfigTestRequest(BaseModel):
    """测试模型连通性请求体。

    Attributes:
        model: 待测试模型名；None 使用当前模型。
        api_key: 临时 API Key（不入库）；None 使用已配置值。
        api_base: 临时 Base URL（不入库）；None 使用已配置值。
    """

    model: str | None = None
    api_key: str | None = None
    api_base: str | None = None


class ProviderCreate(BaseModel):
    """添加/更新提供商请求体。

    Attributes:
        name: 提供商名（如 ``openai``, ``dashscope``）。
        api_key: API Key；None 表示不修改。
        api_base: Base URL；None 表示不修改。
        default_model: 该提供商的默认模型名。
    """

    name: str = Field(min_length=1, max_length=64)
    api_key: str | None = None
    api_base: str | None = None
    default_model: str | None = None


class ModelSwitch(BaseModel):
    """切换模型请求体。

    Attributes:
        model: 目标模型名（litellm 格式，含提供商前缀）。
    """

    model: str = Field(min_length=1, max_length=128)


class PermissionUpdate(BaseModel):
    """更新权限模式请求体。

    Attributes:
        mode: ``readonly``（只读）/ ``ask``（手动审批）/ ``auto``（自动授权）/
            ``deny``（全部拒绝）。
    """

    mode: str = Field(min_length=1, max_length=16)


def _find_dist_dir() -> Path | None:
    """定位前端构建产物目录（webui/dist）。

    Returns:
        dist 目录；不存在返回 None（仅提供 API）。
    """
    dist = _DIST_DIR
    return dist if dist.is_dir() else None


def _extract_token(authorization: str | None, token: str | None) -> str | None:
    """从请求头/query 中提取访问令牌。

    Args:
        authorization: Authorization 头。
        token: query 参数。

    Returns:
        提取到的令牌；无则 None。
    """
    provided: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    return provided or token


def _parse_users() -> dict[str, str | None]:
    """解析用户/令牌映射。

    多用户（``AGENT_WEB_USERS="alice:token1,bob:token2"``）时返回
    ``{token: user}``；单用户（``AGENT_WEB_TOKEN``）返回 ``{token: None}``
    （None 表示使用默认目录，兼容旧版数据路径）；两者都未设置返回空映射（不鉴权）。

    Returns:
        令牌到用户名的映射。
    """
    raw = os.environ.get("AGENT_WEB_USERS", "").strip()
    if raw:
        result: dict[str, str | None] = {}
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry or ":" not in entry:
                continue
            user, token = entry.split(":", 1)
            result[token.strip()] = user.strip()
        return result
    token = os.environ.get("AGENT_WEB_TOKEN", "").strip()
    return {token: None} if token else {}


class UserManagers:
    """按用户懒加载的 SessionManager 池（每用户独立会话目录，线程安全）。"""

    def __init__(self, settings: Settings, store: ProviderStore) -> None:
        self._settings = settings
        self._store = store
        self._managers: dict[str | None, SessionManager] = {}
        self._lock = threading.Lock()

    def for_user(self, user: str | None) -> SessionManager:
        """获取指定用户的 SessionManager（首次访问时创建）。

        Args:
            user: 用户名；None 为默认用户（兼容旧版目录）。

        Returns:
            该用户的会话管理器。
        """
        with self._lock:
            manager = self._managers.get(user)
            if manager is None:
                if user:
                    session_dir = _USER_DIR_TMPL / user / "sessions"
                    manager = SessionManager(
                        self._settings, store=self._store, session_dir=session_dir
                    )
                else:
                    manager = SessionManager(self._settings, store=self._store)
                self._managers[user] = manager
            return manager


def _make_auth_dependency(users: dict[str, str | None]) -> Any:
    """构造按 token 解析用户并注入 ``request.state.user`` 的鉴权依赖。

    Args:
        users: 令牌到用户的映射。

    Returns:
        FastAPI 依赖函数。
    """

    def dependency(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
        token: Annotated[str | None, Query()] = None,
    ) -> None:
        provided = _extract_token(authorization, token)
        if not users:
            request.state.user = None
            return
        if provided not in users:
            raise HTTPException(status_code=401, detail="无效或缺失的访问令牌")
        request.state.user = users[provided]

    return dependency


def create_app(
    settings: Settings | None = None,
    manager: SessionManager | None = None,
    *,
    api_token: str | None | object = _TOKEN_UNSET,
    store: ProviderStore | None = None,
) -> FastAPI:
    """创建 FastAPI 应用。

    Args:
        settings: 全局配置；None 时从环境/配置文件加载。
        manager: 默认用户的会话管理器；None 时基于 settings 构建
            （测试可注入替身，多用户场景请勿注入）。
        api_token: 访问令牌；不传时读取环境变量（``AGENT_WEB_USERS``
            或 ``AGENT_WEB_TOKEN``），传 None 表示显式禁用鉴权。
        store: 运行时配置存储；None 时新建并载入启动配置。

    Returns:
        FastAPI 实例。
    """
    settings = settings or load_settings()
    store = store or ProviderStore()
    store.seed_from_settings(settings)
    if api_token is not _TOKEN_UNSET:
        users: dict[str, str | None] = {api_token: None} if api_token else {}
    else:
        users = _parse_users()
    managers = UserManagers(settings, store)
    if manager is not None:
        managers._managers[None] = manager
    auth = _make_auth_dependency(users)

    app = FastAPI(
        title="果冻",
        version=__version__,
        description="类 Claude Code 的终端 Agent · Web 界面",
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """健康检查（返回当前运行时模型）。"""
        return {"status": "ok", "version": __version__, "model": store.model}

    # ========== Provider 管理 API ==========

    @app.get("/api/providers", dependencies=[Depends(auth)])
    def list_providers(request: Request) -> dict[str, Any]:
        """列出所有已配置的提供商（Key 已掩码）。"""
        return {
            "providers": store.list_providers(),
            "active_model": store.model,
        }

    @app.get("/api/providers/{provider}/models", dependencies=[Depends(auth)])
    def get_provider_models(provider: str, request: Request) -> dict[str, Any]:
        """获取某提供商的可用模型列表。"""
        info = store.get_provider(provider)
        if not info:
            raise HTTPException(status_code=404, detail=f"Provider '{provider}' not found")
        models = store.get_models_for_provider(provider)
        return {
            "provider": provider,
            "models": models,
            "default_model": info.default_model,
        }

    @app.post("/api/providers", dependencies=[Depends(auth)])
    def add_provider(request: Request, body: ProviderCreate) -> dict[str, Any]:
        """添加或更新提供商配置。"""
        if not body.name.strip():
            raise HTTPException(status_code=400, detail="Provider name is required")
        try:
            store.upsert_provider(
                body.name.strip().lower(),
                api_key=body.api_key,
                api_base=body.api_base,
                default_model=body.default_model,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"providers": store.list_providers(), "active_model": store.model}

    @app.delete("/api/providers/{provider}", dependencies=[Depends(auth)])
    def delete_provider(provider: str, request: Request) -> dict[str, Any]:
        """删除提供商配置（当前激活模型若是该提供商会回退到默认）。"""
        store.remove_provider(provider)
        return {"providers": store.list_providers(), "active_model": store.model}

    @app.post("/api/model/switch", dependencies=[Depends(auth)])
    def switch_model(request: Request, body: ModelSwitch) -> dict[str, Any]:
        """切换当前激活模型（热生效，持久化）。"""
        if not body.model.strip():
            raise HTTPException(status_code=400, detail="Model name is required")
        store.set_model(body.model.strip())
        return {"active_model": store.model}

    @app.get("/api/config", dependencies=[Depends(auth)])
    def get_config(request: Request) -> dict[str, Any]:
        """读取运行时配置（API Key 仅返回掩码）。"""
        return {
            "model": store.model,
            "cwd": str(settings.cwd),
            "providers": store.list_providers(),
        }

    @app.put("/api/config", dependencies=[Depends(auth)])
    def update_config(request: Request, body: ConfigUpdate) -> dict[str, Any]:
        """更新运行时配置：切换模型 / 更新提供商 Key 与 Base URL / 切换工作目录（热生效）。"""
        try:
            if body.api_key is not None:
                store.upsert_provider(body.provider, api_key=body.api_key)
            if body.api_base is not None:
                store.upsert_provider(body.provider, api_base=body.api_base)
            if body.model is not None:
                store.set_model(body.model)
            if body.cwd is not None and body.cwd.strip():
                managers.for_user(request.state.user).set_cwd(Path(body.cwd))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "model": store.model,
            "cwd": str(settings.cwd),
            "providers": store.list_providers(),
        }

    @app.post("/api/config/test", dependencies=[Depends(auth)])
    def test_config(request: Request, body: ConfigTestRequest) -> dict[str, Any]:
        """测试模型连通性（最小请求，凭据不入库）。"""
        model = (body.model or store.model).strip()
        if not model:
            raise HTTPException(status_code=400, detail="模型名不能为空")
        _, key, base = store.resolve(model)
        key = body.api_key or key
        base = body.api_base or base
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 64,
            "timeout": 30,
        }
        # 与 LLMClient 保持一致：Qwen 系列关闭思考模式，否则可能因
        # reasoning_content 占满 max_tokens 而测试失败
        if "qwen" in model.lower():
            kwargs.setdefault("chat_template_kwargs", {})["enable_thinking"] = False
        if key:
            kwargs["api_key"] = key
        if base:
            kwargs["api_base"] = base
        if base and is_private_base(base):
            # 内网/环回地址直连：系统代理（Clash 等）会拦截内网请求返回 502
            kwargs["client"] = proxy_bypass_client(base, key)
        start = time.perf_counter()
        try:
            litellm.completion(**kwargs)
        except Exception as exc:  # noqa: BLE001 - 统一映射为可读错误
            return {
                "ok": False,
                "model": model,
                "latency_ms": None,
                "error": str(LLMClient._map_error(exc)),
            }
        latency_ms = int((time.perf_counter() - start) * 1000)
        return {"ok": True, "model": model, "latency_ms": latency_ms}

    @app.get("/api/permissions", dependencies=[Depends(auth)])
    def get_permissions(request: Request) -> dict[str, str]:
        """读取当前权限模式（readonly/ask/auto/deny）。"""
        return {"mode": managers.for_user(request.state.user).permission_mode}

    @app.put("/api/permissions", dependencies=[Depends(auth)])
    def update_permissions(request: Request, body: PermissionUpdate) -> dict[str, str]:
        """切换权限模式（热生效，对后续对话轮次生效）。"""
        try:
            mode = managers.for_user(request.state.user).set_permission_mode(body.mode)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"mode": mode}

    @app.get("/api/files", dependencies=[Depends(auth)])
    def list_workspace_files(request: Request, path: str = Query(default=".")) -> dict[str, Any]:
        """列出工作区内目录（工作台文件树数据源，只读）。"""
        root = settings.cwd.resolve()
        target = _workspace_path(settings.cwd, path)
        if not target.is_dir():
            raise HTTPException(status_code=404, detail=f"目录不存在: {path}")
        try:
            children = list(target.iterdir())
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"无法读取目录: {exc}") from exc
        entries: list[dict[str, Any]] = []
        for child in children:
            try:
                is_dir = child.is_dir()
            except OSError:
                continue
            entry: dict[str, Any] = {"name": child.name, "type": "dir" if is_dir else "file"}
            if not is_dir:
                try:
                    entry["size"] = child.stat().st_size
                except OSError:
                    entry["size"] = None
            entries.append(entry)
        entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
        rel = "." if target == root else target.relative_to(root).as_posix()
        return {"cwd": root.name, "path": rel, "entries": entries}

    @app.get("/api/file", dependencies=[Depends(auth)])
    def read_workspace_file(request: Request, path: str) -> dict[str, Any]:
        """读取工作区内文本文件内容（工作台预览数据源，只读）。"""
        root = settings.cwd.resolve()
        target = _workspace_path(settings.cwd, path)
        if not target.is_file():
            raise HTTPException(status_code=404, detail=f"文件不存在: {path}")
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"无法读取文件: {exc}") from exc
        is_binary = b"\x00" in data[:4096]
        content: str | None = None
        truncated = False
        if not is_binary:
            content = data.decode("utf-8", errors="replace")
            if len(content) > _MAX_PREVIEW_CHARS:
                content = content[:_MAX_PREVIEW_CHARS]
                truncated = True
        return {
            "path": target.relative_to(root).as_posix(),
            "name": target.name,
            "size": target.stat().st_size,
            "is_binary": is_binary,
            "truncated": truncated,
            "content": content,
        }

    @app.get("/api/file/raw", dependencies=[Depends(auth)])
    def read_workspace_file_raw(request: Request, path: str) -> FileResponse:
        """返回工作区内文件的原始字节（图片预览 / 下载）。"""
        target = _workspace_path(settings.cwd, path)
        if not target.is_file():
            raise HTTPException(status_code=404, detail=f"文件不存在: {path}")
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(target, media_type=media_type, filename=target.name)

    @app.get("/api/sessions", dependencies=[Depends(auth)])
    def list_sessions(request: Request) -> dict[str, Any]:
        """列出当前用户的全部会话。"""
        return {"sessions": managers.for_user(request.state.user).list_sessions()}

    @app.post("/api/sessions", dependencies=[Depends(auth)])
    def create_session(request: Request) -> dict[str, str]:
        """新建会话（使用当前运行时模型）。"""
        session = managers.for_user(request.state.user).create_session()
        return {"session_id": session.session_id}

    @app.patch(
        "/api/sessions/{session_id}",
        dependencies=[Depends(auth)],
    )
    def rename_session(request: Request, session_id: str, body: RenameRequest) -> dict[str, str]:
        """重命名会话（会话不存在 404，标题非法 400）。"""
        mgr = managers.for_user(request.state.user)
        try:
            mgr.get_session(session_id)
        except SessionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            session = mgr.rename_session(session_id, body.title)
        except SessionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"session_id": session.session_id, "title": session.title}

    @app.delete(
        "/api/sessions/{session_id}/skill",
        dependencies=[Depends(auth)],
    )
    def clear_session_skill(request: Request, session_id: str) -> dict[str, Any]:
        """清除会话当前激活的 skill（前端 ✕ 按钮）。"""
        mgr = managers.for_user(request.state.user)
        try:
            cleared = mgr.clear_skill(session_id)
        except SessionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"cleared": cleared}

    @app.delete(
        "/api/sessions/{session_id}",
        dependencies=[Depends(auth)],
    )
    def delete_session(request: Request, session_id: str) -> dict[str, str]:
        """删除会话。"""
        mgr = managers.for_user(request.state.user)
        try:
            mgr.delete_session(session_id)
        except SessionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": session_id}

    @app.get("/api/skills", dependencies=[Depends(auth)])
    def list_skills(request: Request) -> dict[str, Any]:
        """列出所有可用的 skills。"""
        from agent_shell.skills import list_skills

        return {"skills": list_skills()}

    @app.post("/api/skills/install", dependencies=[Depends(auth)])
    def install_skill(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """安装 skill。

        Body:
        - ``{"url": "..."}``: GitHub 仓库 URL / raw URL / 本地文件或目录路径
        - 或 ``{"name","description","triggers","prompt_template"}``: 直接参数定义
        """
        from agent_shell.skills.installer import (
            install_skill_from_definition,
            install_skill_from_url,
        )
        from agent_shell.skills.registry import reload_global_registry

        try:
            if "url" in body:
                definition = install_skill_from_url(body["url"])
            else:
                definition = install_skill_from_definition(
                    name=body.get("name", ""),
                    description=body.get("description", ""),
                    triggers=body.get("triggers", []),
                    prompt_template=body.get("prompt_template", ""),
                    body=body.get("body", ""),
                    author=body.get("author", ""),
                )
            count = reload_global_registry()
            return {
                "success": True,
                "skill": {
                    "name": definition.name,
                    "description": definition.description,
                    "triggers": definition.normalize_triggers(),
                },
                "skills_count": count,
            }
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/api/skills/reload", dependencies=[Depends(auth)])
    def reload_skills(request: Request) -> dict[str, Any]:
        """重新加载所有 skills（无需重启服务）。"""
        from agent_shell.skills import list_skills
        from agent_shell.skills.registry import reload_global_registry

        reload_global_registry()
        return {
            "success": True,
            "skills": list_skills(),
        }

    @app.delete("/api/skills/{name}", dependencies=[Depends(auth)])
    def uninstall_skill(request: Request, name: str) -> dict[str, Any]:
        """卸载 skill。"""
        from agent_shell.skills import list_skills
        from agent_shell.skills.installer import uninstall_skill
        from agent_shell.skills.registry import reload_global_registry

        if uninstall_skill(name):
            reload_global_registry()
            return {
                "success": True,
                "message": f"Skill '{name}' uninstalled",
                "skills": list_skills(),
            }
        else:
            raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    @app.get(
        "/api/sessions/{session_id}/messages",
        dependencies=[Depends(auth)],
    )
    def session_messages(request: Request, session_id: str) -> dict[str, Any]:
        """获取会话历史消息。"""
        mgr = managers.for_user(request.state.user)
        try:
            session = mgr.get_session(session_id)
        except SessionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"messages": mgr.serialize_messages(session)}

    @app.websocket("/ws/{session_id}")
    async def chat_websocket(websocket: WebSocket, session_id: str) -> None:
        """WebSocket 对话通道：接收用户消息/停止指令，流式返回事件。"""
        if users:
            token = websocket.query_params.get("token")
            if token not in users:
                await websocket.close(code=1008, reason="无效或缺失的访问令牌")
                return
            user = users[token]
        else:
            user = None
        await websocket.accept()
        mgr = managers.for_user(user)
        agent_task: asyncio.Task | None = None

        async def handle_approval_decision(raw: dict) -> None:
            """回填权限决策；无匹配的待决请求时告知前端。"""
            resolved = mgr.resolve_approval(
                session_id, str(raw.get("id", "")), str(raw.get("decision", ""))
            )
            if not resolved:
                await websocket.send_json(
                    {"type": "error", "message": "当前没有等待审批的权限请求"}
                )

        try:
            while True:
                if agent_task is None:
                    # 等待用户消息
                    raw = await websocket.receive_json()
                    if raw.get("type") == "stop":
                        await websocket.send_json({"type": "stopped", "message": "已停止"})
                        continue
                    if raw.get("type") == "approval_decision":
                        await handle_approval_decision(raw)
                        continue
                    message = ClientMessage.model_validate(raw)
                    agent_task = asyncio.create_task(
                        mgr.run_agent(
                            session_id,
                            message.content,
                            lambda event: websocket.send_json(event.model_dump()),
                        )
                    )
                else:
                    # Agent运行中，同时监听停止消息和agent完成
                    receive_task = asyncio.create_task(websocket.receive_json())
                    done, pending = await asyncio.wait(
                        {agent_task, receive_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if agent_task in done:
                        # Agent自然完成
                        agent_task = None
                    else:
                        # 收到消息（停止指令 / 权限决策 / 新消息）
                        try:
                            raw = receive_task.result()
                        except Exception:
                            raw = {}
                        if raw.get("type") == "stop":
                            # 请求取消并立即响应，让agent自然停止
                            mgr.request_cancel(session_id)
                            await websocket.send_json({"type": "stopped", "message": "已停止"})
                        elif raw.get("type") == "approval_decision":
                            await handle_approval_decision(raw)
                        else:
                            # Agent运行中收到新消息：明确告知而非静默丢弃
                            await websocket.send_json(
                                {
                                    "type": "error",
                                    "message": "当前会话正在运行中，请等待本轮完成后再发送",
                                }
                            )
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001 - 连接级兜底，记录后断开
            await websocket.close(code=1011, reason=f"{type(exc).__name__}: {exc}")
        finally:
            # 断开/退出时取消仍在运行的轮次，避免权限确认等阻塞点悬挂 worker 线程
            mgr.request_cancel(session_id)
            if agent_task is not None:
                agent_task.cancel()

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


app = create_app()
