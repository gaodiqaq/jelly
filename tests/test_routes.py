"""server 层：运行时配置 API 与多用户隔离测试（无网络）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_shell.config import PermissionsConfig, Settings
from agent_shell.runtime import ProviderStore
from agent_shell.server import app as app_module
from agent_shell.server.app import create_app
from agent_shell.server.manager import SessionManager
from agent_shell.types import AssistantMessage


@pytest.fixture()
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    """隔离环境：认证禁用或显式配置，用户目录落在 tmp_path。"""
    monkeypatch.delenv("AGENT_WEB_TOKEN", raising=False)
    monkeypatch.delenv("AGENT_WEB_USERS", raising=False)
    monkeypatch.setattr(app_module, "_USER_DIR_TMPL", tmp_path / "users")
    return Settings(
        model="scripted-model",
        session_dir=tmp_path / "sessions",
        cwd=tmp_path,
        permissions=PermissionsConfig(default="auto", auto_approve_read_only=True),
        max_turns=5,
    )


def make_app(settings: Settings, tmp_path: Path) -> tuple[object, ProviderStore]:
    """构造注入脚本化 LLM 的应用与隔离 store。"""
    store = ProviderStore(tmp_path / "runtime.yaml")
    llm = ScriptedLLM([AssistantMessage(content="ok")])
    manager = SessionManager(settings, llm, store=store)
    return create_app(settings, manager, store=store), store


class ScriptedLLM:
    """简单的脚本化 LLM 替身。"""

    def __init__(self, replies: list[AssistantMessage]) -> None:
        self._replies = list(replies)
        self.model = "scripted"

    def complete(self, messages, tools=None, *, stream=True, on_token=None):
        reply = self._replies.pop(0)
        if on_token is not None and reply.content:
            on_token(reply.content)
        return reply


def test_get_config_masks_keys(settings: Settings, tmp_path: Path) -> None:
    """GET /api/config 返回模型与掩码 Key，不泄露明文。"""
    app, store = make_app(settings, tmp_path)
    store.upsert_provider("openai", api_key="sk-super-secret-99")
    client = TestClient(app)
    data = client.get("/api/config").json()
    assert data["model"] == "scripted-model"
    providers = data["providers"]
    assert providers[0]["name"] == "openai"
    assert providers[0]["has_key"] is True
    assert providers[0]["api_key_masked"] == "sk-***t-99"
    assert "sk-super-secret-99" not in str(data)


def test_put_config_updates_and_persists(settings: Settings, tmp_path: Path) -> None:
    """PUT 更新模型与 Key 后 GET 反映，且重新加载 store 仍保持。"""
    app, store = make_app(settings, tmp_path)
    client = TestClient(app)
    resp = client.put(
        "/api/config",
        json={"provider": "deepseek", "model": "deepseek/deepseek-chat", "api_key": "sk-ds-key"},
    )
    assert resp.status_code == 200
    data = client.get("/api/config").json()
    assert data["model"] == "deepseek/deepseek-chat"
    deepseek = next(p for p in data["providers"] if p["name"] == "deepseek")
    assert deepseek["api_key_masked"] == "***-key"

    reloaded = ProviderStore(tmp_path / "runtime.yaml")
    assert reloaded.model == "deepseek/deepseek-chat"
    assert reloaded.get_provider("deepseek").api_key == "sk-ds-key"


def test_put_config_rejects_empty_model(settings: Settings, tmp_path: Path) -> None:
    """空模型名返回 400。"""
    app, _ = make_app(settings, tmp_path)
    client = TestClient(app)
    resp = client.put("/api/config", json={"provider": "openai", "model": "   "})
    assert resp.status_code == 400


def test_config_test_ok(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """连通性测试成功：返回延迟与模型。"""
    monkeypatch.setattr(app_module.litellm, "completion", lambda **kw: None)
    app, _ = make_app(settings, tmp_path)
    client = TestClient(app)
    data = client.post("/api/config/test", json={"model": "openai/gpt-4o-mini"}).json()
    assert data["ok"] is True
    assert data["model"] == "openai/gpt-4o-mini"
    assert isinstance(data["latency_ms"], int)


def test_config_test_failed(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """连通性失败返回错误信息（中文可读）。"""
    def boom(model=None, **kw):
        raise app_module.litellm.exceptions.AuthenticationError(
            message="bad key", model=None, llm_provider_name="openai"
        )

    monkeypatch.setattr(app_module.litellm, "completion", boom)
    app, _ = make_app(settings, tmp_path)
    client = TestClient(app)
    data = client.post("/api/config/test", json={"model": "openai/gpt-4o-mini"}).json()
    assert data["ok"] is False
    assert data["latency_ms"] is None
    assert data["error"]


def test_users_isolation(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """多用户：各自会话列表与历史互不可见。"""
    monkeypatch.setenv("AGENT_WEB_USERS", "alice:tok-a,bob:tok-b")
    app, _ = make_app(settings, tmp_path)
    client = TestClient(app)

    alice_headers = {"Authorization": "Bearer tok-a"}
    bob_headers = {"Authorization": "Bearer tok-b"}

    alice_session = client.post("/api/sessions", headers=alice_headers).json()["session_id"]
    bob_session = client.post("/api/sessions", headers=bob_headers).json()["session_id"]

    alice_ids = {
        s["session_id"]
        for s in client.get("/api/sessions", headers=alice_headers).json()["sessions"]
    }
    bob_ids = {
        s["session_id"]
        for s in client.get("/api/sessions", headers=bob_headers).json()["sessions"]
    }
    assert alice_session in alice_ids and bob_session not in alice_ids
    assert bob_session in bob_ids and alice_session not in bob_ids

    bob_msgs = client.get(
        f"/api/sessions/{bob_session}/messages", headers=alice_headers
    )
    assert bob_msgs.status_code == 404


def test_auth_rejects_unknown_token(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未知令牌被拒绝（401）；无令牌且配置了用户时同样拒绝。"""
    monkeypatch.setenv("AGENT_WEB_USERS", "alice:tok-a")
    app, _ = make_app(settings, tmp_path)
    client = TestClient(app)
    assert client.get("/api/sessions", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/api/config", headers={"Authorization": "Bearer tok-a"}).status_code == 200