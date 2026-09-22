"""Project boundaries exercised through the real API and executor, without network."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from agent_shell.config import PermissionsConfig, Settings
from agent_shell.core.agent import AgentCallbacks
from agent_shell.core.session import Session
from agent_shell.errors import SessionError
from agent_shell.llm.client import LLMClient
from agent_shell.runtime import ProviderStore
from agent_shell.server import app as app_module
from agent_shell.server.app import create_app
from agent_shell.server.manager import SessionManager
from agent_shell.server.projects import ProjectSettings, ProjectStore
from agent_shell.types import AssistantMessage, ToolCall


class FileLLM:
    model = "test"

    def complete(self, messages, tools=None, **kwargs):
        if messages[-1].role == "user":
            return AssistantMessage(
                tool_calls=[
                    ToolCall(
                        id="write",
                        name="write",
                        arguments={
                            "path": "result.md",
                            "content": messages[-1].content,
                            "overwrite": True,
                        },
                    )
                ]
            )
        return AssistantMessage(content="完成")


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_WEB_USERS", raising=False)
    settings = Settings(
        cwd=tmp_path,
        session_dir=tmp_path / "sessions",
        permissions=PermissionsConfig(default="readonly"),
    )
    store = ProviderStore(tmp_path / "runtime.yaml")
    manager = SessionManager(settings, FileLLM(), store=store)
    with TestClient(create_app(settings, manager, api_token=None, store=store)) as client:
        yield client, manager, tmp_path


def project(studio, name, permission="auto"):
    client, _, root = studio
    cwd = root / name
    cwd.mkdir()
    response = client.post(
        "/api/projects",
        json={
            "name": name,
            "cwd": str(cwd),
            "model": "test",
            "permission": permission,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    sid = client.post("/api/sessions", json={"project_id": data["id"]}).json()["session_id"]
    return data, sid, cwd


def run(client, sid, content):
    with client.websocket_connect(f"/ws/{sid}") as ws:
        ws.send_json({"type": "user_message", "content": content})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "done":
                assert not any(e["type"] == "error" for e in events), events
                return events


def test_same_named_artifacts_and_restore_are_project_scoped(studio):
    client, _, _ = studio
    a, sid_a, dir_a = project(studio, "alpha")
    b, sid_b, dir_b = project(studio, "beta")
    run(client, sid_a, "Alpha original")
    run(client, sid_a, "Alpha revised")
    run(client, sid_b, "Beta original")
    for sid, expected in [(sid_a, "Alpha revised"), (sid_b, "Beta original")]:
        params = {"path": "result.md", "session_id": sid}
        response = client.get("/api/file", params=params)
        assert response.status_code == 200, response.text
        assert response.json()["content"] == expected
        assert client.get("/api/file/raw", params=params).text == expected
    artifacts = client.get(f"/api/sessions/{sid_a}/artifacts").json()["artifacts"]
    assert [(f["path"], f["version"]) for f in artifacts] == [("result.md", 2)]
    changes = client.get(f"/api/sessions/{sid_a}/changes").json()["changes"]
    response = client.post(f"/api/sessions/{sid_a}/changes/{changes[0]['id']}/restore")
    assert response.status_code == 200, response.text
    assert (dir_a / "result.md").read_text() == "Alpha original"
    assert (dir_b / "result.md").read_text() == "Beta original"
    assert (
        client.get(
            "/api/file",
            params={
                "path": "result.md",
                "session_id": sid_a,
                "project_id": b["id"],
            },
        ).status_code
        >= 400
    )
    assert (
        client.get(
            "/api/file",
            params={
                "path": "../beta/result.md",
                "project_id": a["id"],
            },
        ).status_code
        == 400
    )


def test_project_settings_and_session_root_survive_reload(studio):
    client, manager, root = studio
    data, sid, cwd = project(studio, "persistent")
    original = manager.get_session(sid)
    response = client.put(
        f"/api/projects/{data['id']}",
        json={
            "name": "Renamed",
            "model": "provider/next",
            "permission": "readonly",
            "cwd": str(root),
        },
    )
    assert response.json()["cwd"] == str(cwd.resolve())
    resumed = Session.resume(manager._session_dir, sid)
    assert resumed.project_id == data["id"]
    assert resumed.cwd == cwd.resolve()
    assert resumed.created_at == original.created_at
    assert ProjectStore(manager.projects.directory).get(data["id"])["model"] == "provider/next"
    manager.set_cwd(root)
    assert manager.get_session(sid).cwd == cwd.resolve()
    assert client.get("/api/projects").json()["projects"][0]["task_count"] == 1


def test_project_readonly_overrides_other_settings(studio):
    client, manager, _ = studio
    data, sid, cwd = project(studio, "readonly", permission="readonly")
    events = run(client, sid, "must not be written")
    assert any(e["type"] == "tool_result" and e["is_error"] for e in events)
    assert not (cwd / "result.md").exists()
    assert client.get(f"/api/sessions/{sid}/artifacts").json()["artifacts"] == []
    manager.projects.save(ProjectSettings(name="readonly", permission="auto"), data["id"])
    run(client, sid, "allowed")
    change = manager.changes(sid).list()[0]
    manager.projects.save(ProjectSettings(name="readonly", permission="readonly"), data["id"])
    assert client.post(f"/api/sessions/{sid}/changes/{change['id']}/restore").status_code == 409
    manager.projects.save(ProjectSettings(name="readonly", permission="auto"), data["id"])
    assert client.post(f"/api/sessions/{sid}/changes/{change['id']}/restore").status_code == 200
    assert client.get(f"/api/sessions/{sid}/artifacts").json()["artifacts"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "bad", "cwd": "relative/path"},
        {"name": " ", "cwd": "C:/"},
        {"name": "bad", "cwd": "C:/missing-jelly-project-directory"},
    ],
)
def test_invalid_project_does_not_create_session(studio, payload):
    client, _, _ = studio
    assert client.post("/api/projects", json=payload).status_code == 400
    assert client.get("/api/projects").json()["projects"] == []
    assert client.post("/api/sessions", json={"project_id": "0" * 32}).status_code >= 400


def test_concurrent_project_runs_keep_their_own_roots(studio):
    _, manager, _ = studio
    _, sid_a, dir_a = project(studio, "concurrent-a")
    _, sid_b, dir_b = project(studio, "concurrent-b")

    async def execute():
        async def emit(_):
            pass

        await asyncio.gather(
            manager.run_agent(sid_a, "A", emit), manager.run_agent(sid_b, "B", emit)
        )

    asyncio.run(execute())
    assert (dir_a / "result.md").read_text() == "A"
    assert (dir_b / "result.md").read_text() == "B"
    assert not manager._running and not manager._cancel


def test_missing_directory_cleans_up_run_state(studio):
    _, manager, _ = studio
    _, sid, cwd = project(studio, "removed")
    cwd.rmdir()

    async def execute():
        async def emit(_):
            pass

        with pytest.raises(SessionError):
            await manager.run_agent(sid, "hello", emit)

    asyncio.run(execute())
    assert not manager._running and not manager._cancel


def test_project_catalogues_are_user_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WEB_USERS", "alice:a,bob:b")
    monkeypatch.setattr(app_module, "_USER_DIR_TMPL", tmp_path / "users")
    settings = Settings(cwd=tmp_path, session_dir=tmp_path / "sessions")
    store = ProviderStore(tmp_path / "runtime.yaml")
    with TestClient(create_app(settings, store=store)) as client:
        data = client.post(
            "/api/projects",
            headers={"Authorization": "Bearer a"},
            json={"name": "Alice", "cwd": str(tmp_path)},
        ).json()
        headers = {"Authorization": "Bearer b"}
        assert client.get("/api/projects", headers=headers).json()["projects"] == []
        assert (
            client.post(
                "/api/sessions", headers=headers, json={"project_id": data["id"]}
            ).status_code
            >= 400
        )
        assert (
            client.get("/api/files", headers=headers, params={"project_id": data["id"]}).status_code
            >= 400
        )


def test_project_run_freezes_model_credentials_and_permission(studio):
    _, manager, _ = studio
    data, sid, cwd = project(studio, "snapshot")
    manager._store.upsert_provider("openai", api_key="before-key")
    manager._store.set_model("openai/global")
    manager._llm = LLMClient(manager._settings, manager._store)
    manager.projects.save(
        ProjectSettings(name="snapshot", model="openai/project", permission="auto"), data["id"]
    )
    before = manager._build_agent(manager.get_session(sid), AgentCallbacks())
    manager.projects.save(
        ProjectSettings(name="snapshot", model="openai/next", permission="readonly"), data["id"]
    )
    manager._store.upsert_provider("openai", api_key="after-key")
    after = manager._build_agent(manager.get_session(sid), AgentCallbacks())
    assert before.llm.model == "openai/project"
    assert before.llm._resolve()[1] == "before-key"
    assert after.llm.model == "openai/next"
    assert after.llm._resolve()[1] == "after-key"
    assert manager._store.model == "openai/global"
    call = ToolCall(id="frozen", name="write", arguments={"path": "frozen.md", "content": "ok"})
    assert not before.executor.execute(call).is_error
    assert after.executor.execute(call).is_error
    assert (cwd / "frozen.md").read_text() == "ok"
