"""A task delivery bundle contains only scoped, bounded work products."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

from fastapi.testclient import TestClient

from agent_shell.config import PermissionsConfig, Settings
from agent_shell.runtime import ProviderStore
from agent_shell.server.app import create_app
from agent_shell.server.manager import SessionManager
from agent_shell.types import AssistantMessage, ToolCall, ToolResult, UserMessage


def _client(tmp_path, *, api_token=None):
    settings = Settings(
        cwd=tmp_path,
        session_dir=tmp_path / "sessions",
        permissions=PermissionsConfig(default="auto"),
    )
    store = ProviderStore(tmp_path / "runtime.yaml")
    manager = SessionManager(settings, store=store)
    client = TestClient(create_app(settings, manager, api_token=api_token, store=store))
    return client, manager, store


def _record_file(manager, sid, name, content):
    target = manager.workspace(sid) / name

    def write():
        target.write_bytes(content)
        return ToolResult(content="written", is_error=False)

    result = manager.changes(sid).execute(
        ToolCall(id=name, name="write", arguments={"path": name}), write
    )
    assert not result.is_error


def test_delivery_contains_transcript_and_scoped_artifacts_without_provider_credentials(
    tmp_path, monkeypatch
):
    from agent_shell.server import project_routes

    created = []
    original = project_routes.create_task_bundle

    def capture(*args):
        path = original(*args)
        created.append(path)
        return path

    monkeypatch.setattr(project_routes, "create_task_bundle", capture)
    client, manager, store = _client(tmp_path, api_token="private-access")
    store.upsert_provider("openai", api_key="provider-secret-must-stay-local")
    session = manager.create_session()
    session.set_title("产品交付")
    session.add_message(UserMessage(content="请制作产品方案"))
    session.add_message(AssistantMessage(content="方案已经写好。"))
    session.save()
    _record_file(manager, session.session_id, "方案.md", "# 成果\n".encode())
    _record_file(manager, session.session_id, ".env", b"LOCAL_SECRET=do-not-export")
    (tmp_path / "unrelated.txt").write_text("other work", encoding="utf-8")

    endpoint = f"/api/sessions/{session.session_id}/delivery"
    assert client.get(endpoint).status_code == 401
    response = client.get(endpoint, headers={"Authorization": "Bearer private-access"})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")
    assert response.headers["cache-control"] == "no-store"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert names == {"task.md", "provenance.json", "manifest.json", "artifacts/方案.md"}
        report = archive.read("task.md").decode("utf-8")
        assert "请制作产品方案" in report and "方案已经写好" in report
        assert archive.read("artifacts/方案.md") == "# 成果\n".encode()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == "jelly-task-delivery"
        assert manifest["files"][0]["sha256"] == hashlib.sha256("# 成果\n".encode()).hexdigest()
        assert manifest["omitted"] == [{"path": ".env", "reason": "常见敏感文件"}]
        provenance = json.loads(archive.read("provenance.json"))
        assert {entry["path"] for entry in provenance} == {"方案.md", ".env"}
    assert b"provider-secret-must-stay-local" not in response.content
    assert b"LOCAL_SECRET=do-not-export" not in response.content
    assert b"other work" not in response.content
    assert len(created) == 1 and not created[0].exists()


def test_delivery_rejects_active_task_and_large_artifact(tmp_path, monkeypatch):
    from agent_shell.server import delivery

    client, manager, _ = _client(tmp_path)
    session = manager.create_session()
    _record_file(manager, session.session_id, "large.txt", b"123456")
    endpoint = f"/api/sessions/{session.session_id}/delivery"
    manager._running.add(session.session_id)
    assert client.get(endpoint).status_code == 409
    manager._running.discard(session.session_id)
    monkeypatch.setattr(delivery, "_MAX_FILE_BYTES", 3)
    response = client.get(endpoint)
    assert response.status_code == 400
    assert "大小上限" in response.json()["detail"]


def test_delivery_never_archives_a_path_outside_the_workspace(tmp_path):
    client, manager, _ = _client(tmp_path)
    session = manager.create_session()
    outside = tmp_path.parent / f"{tmp_path.name}-outside-delivery.txt"
    outside.write_text("must remain outside", encoding="utf-8")
    directory = manager._session_dir / "changes" / session.session_id
    directory.mkdir(parents=True)
    record = {
        "id": "a" * 32,
        "path": f"../{outside.name}",
        "root": str(tmp_path.resolve()),
        "tool": "write",
        "state": "ready",
        "created_at": "2026-01-01T00:00:00+00:00",
        "before": None,
        "after": None,
    }
    (directory / f"{record['id']}.json").write_text(json.dumps(record), encoding="utf-8")
    response = client.get(f"/api/sessions/{session.session_id}/delivery")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert all(not name.startswith("artifacts/") for name in archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["omitted"] == []
        assert manifest["warnings"] == [
            {"file": f"{record['id']}.json", "message": "版本记录损坏，已隔离"}
        ]


def test_corrupt_version_warns_without_hiding_other_artifacts(tmp_path):
    client, manager, _ = _client(tmp_path)
    session = manager.create_session()
    _record_file(manager, session.session_id, "good.md", b"good")
    directory = manager.changes(session.session_id).directory
    corrupt = directory / ("b" * 32 + ".json")
    corrupt.write_text("{broken", encoding="utf-8")

    changes = client.get(f"/api/sessions/{session.session_id}/changes")
    artifacts = client.get(f"/api/sessions/{session.session_id}/artifacts")
    delivery = client.get(f"/api/sessions/{session.session_id}/delivery")
    assert changes.status_code == artifacts.status_code == delivery.status_code == 200
    assert len(changes.json()["changes"]) == 1
    assert [item["path"] for item in artifacts.json()["artifacts"]] == ["good.md"]
    warning = [{"file": corrupt.name, "message": "版本记录损坏，已隔离"}]
    assert changes.json()["warnings"] == artifacts.json()["warnings"] == warning
    with zipfile.ZipFile(io.BytesIO(delivery.content)) as archive:
        assert archive.read("artifacts/good.md") == b"good"
        assert json.loads(archive.read("manifest.json"))["warnings"] == warning
