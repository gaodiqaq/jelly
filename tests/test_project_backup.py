"""A complete project backup can be verified and restored without overwriting data."""

from __future__ import annotations

import hashlib
import io
import json
import struct
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_shell.config import PermissionsConfig, Settings
from agent_shell.runtime import ProviderStore
from agent_shell.server import project_backup as backup_module
from agent_shell.server import project_routes as project_routes_module
from agent_shell.server.app import create_app
from agent_shell.server.manager import SessionManager
from agent_shell.types import AssistantMessage, ToolCall, ToolResult, UserMessage

_BACKUP_HEADERS = {
    "Content-Type": "application/zip",
    "X-Jelly-Intent": "project-backup",
}


def _studio(tmp_path, monkeypatch, *, token=None):
    monkeypatch.setenv("AGENT_PROJECTS_ROOT", str(tmp_path / "managed"))
    settings = Settings(
        cwd=tmp_path,
        session_dir=tmp_path / "sessions",
        permissions=PermissionsConfig(default="auto"),
    )
    store = ProviderStore(tmp_path / "runtime.yaml")
    manager = SessionManager(settings, store=store)
    return TestClient(create_app(settings, manager, api_token=token, store=store)), manager, store


def _seed(client, manager):
    response = client.post(
        "/api/projects/managed", json={"name": "设计方案", "permission": "auto"}
    )
    assert response.status_code == 200, response.text
    project = response.json()
    session = manager.create_session(project["id"])
    session.set_title("第一份方案")
    session.add_message(UserMessage(content="完成方案"))
    session.add_message(AssistantMessage(content="已经完成。"))
    session.save()
    root = manager.workspace(session.session_id)
    (root / "empty").mkdir()
    (root / ".env").write_text("PRIVATE=workspace-data", encoding="utf-8")
    target = root / "方案.md"
    target.write_text("旧方案", encoding="utf-8")

    def write():
        target.write_text("新方案", encoding="utf-8")
        return ToolResult(content="done")

    result = manager.changes(session.session_id).execute(
        ToolCall(id="write", name="write", arguments={"path": "方案.md"}), write
    )
    assert not result.is_error
    return project, session, root


def _replace_session_meta(content: bytes, session_id: str, replacement: object) -> bytes:
    with zipfile.ZipFile(io.BytesIO(content)) as source:
        infos = source.infolist()
        payloads = {info.filename: source.read(info.filename) for info in infos}
    name = f"sessions/{session_id}.jsonl"
    _first, separator, rest = payloads[name].partition(b"\n")
    if isinstance(replacement, dict):
        meta = json.loads(_first)
        meta.update(replacement)
    else:
        meta = replacement
    payloads[name] = json.dumps(meta, ensure_ascii=False).encode("utf-8") + separator + rest
    manifest = json.loads(payloads["manifest.json"])
    entry = next(item for item in manifest["files"] if item["path"] == name)
    entry["size"] = len(payloads[name])
    entry["sha256"] = hashlib.sha256(payloads[name]).hexdigest()
    payloads["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for info in infos:
            archive.writestr(info, payloads[info.filename])
    return output.getvalue()


def test_project_backup_roundtrip_preserves_work_and_versions(tmp_path, monkeypatch):
    client, manager, store = _studio(tmp_path, monkeypatch)
    store.upsert_provider("openai", api_key="provider-secret-must-not-export")
    original, session, root = _seed(client, manager)
    corrupt_record = manager.changes(session.session_id).directory / "broken.json"
    corrupt_record.write_text("{broken", encoding="utf-8")
    backup = client.get(
        f"/api/projects/{original['id']}/backup", headers=_BACKUP_HEADERS
    )
    assert backup.status_code == 200, backup.text
    assert backup.headers["content-type"].startswith("application/zip")
    assert b"provider-secret-must-not-export" not in backup.content
    with zipfile.ZipFile(io.BytesIO(backup.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == "jelly-project-backup"
        assert "empty" in manifest["directories"]
        assert archive.read("workspace/.env") == b"PRIVATE=workspace-data"
        assert archive.read("workspace/方案.md") == "新方案".encode()
        assert f"sessions/{session.session_id}.jsonl" in archive.namelist()

    imported = client.post(
        "/api/projects/import", content=backup.content,
        headers=_BACKUP_HEADERS,
    )
    assert imported.status_code == 201, imported.text
    restored = imported.json()["project"]
    assert imported.json()["task_count"] == 1
    assert restored["id"] != original["id"]
    assert restored["cwd"] != str(root)
    assert restored["permission"] == "ask"
    assert restored["archived_at"] is None
    restored_root = Path(restored["cwd"])
    assert (restored_root / "empty").is_dir()
    assert (restored_root / ".env").read_text(encoding="utf-8") == "PRIVATE=workspace-data"
    assert (restored_root / "方案.md").read_text(encoding="utf-8") == "新方案"
    restored_session = next(
        item for item in manager.list_sessions() if item["project_id"] == restored["id"]
    )
    sid = restored_session["session_id"]
    assert sid != session.session_id
    assert manager.get_session(sid).title == "第一份方案"
    versions = client.get(f"/api/sessions/{sid}/changes").json()
    changes = versions["changes"]
    assert len(changes) == 1 and changes[0]["state"] == "ready"
    assert versions["warnings"] == [
        {"file": "broken.json", "message": "版本记录损坏，已隔离"}
    ]
    response = client.post(f"/api/sessions/{sid}/changes/{changes[0]['id']}/restore")
    assert response.status_code == 200, response.text
    assert (restored_root / "方案.md").read_text(encoding="utf-8") == "旧方案"
    assert (root / "方案.md").read_text(encoding="utf-8") == "新方案"

    second = client.post(
        "/api/projects/import", content=backup.content, headers=_BACKUP_HEADERS
    )
    assert second.status_code == 201
    assert second.json()["project"]["cwd"] != restored["cwd"]


def test_project_import_rejects_corruption_and_traversal_without_creating_project(
    tmp_path, monkeypatch
):
    client, manager, _ = _studio(tmp_path, monkeypatch)
    original, _, _ = _seed(client, manager)
    backup = client.get(
        f"/api/projects/{original['id']}/backup", headers=_BACKUP_HEADERS
    ).content
    original_projects = len(manager.projects.list())
    with zipfile.ZipFile(io.BytesIO(backup)) as source:
        tampered = io.BytesIO()
        with zipfile.ZipFile(tampered, "w") as output:
            for info in source.infolist():
                content = source.read(info.filename)
                if info.filename == "workspace/方案.md":
                    content = b"x" * len(content)
                output.writestr(info, content)
    response = client.post(
        "/api/projects/import", content=tampered.getvalue(), headers=_BACKUP_HEADERS
    )
    assert response.status_code == 400
    assert "校验失败" in response.json()["detail"]
    assert len(manager.projects.list()) == original_projects

    traversal = io.BytesIO()
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../outside.txt", "bad")
        archive.writestr("manifest.json", "{}")
    response = client.post(
        "/api/projects/import", content=traversal.getvalue(), headers=_BACKUP_HEADERS
    )
    assert response.status_code == 400
    assert len(manager.projects.list()) == original_projects
    assert not (tmp_path / "outside.txt").exists()


def test_running_project_cannot_be_exported(tmp_path, monkeypatch):
    client, manager, _ = _studio(tmp_path, monkeypatch)
    project, session, _ = _seed(client, manager)
    manager._running.add(session.session_id)
    response = client.get(
        f"/api/projects/{project['id']}/backup", headers=_BACKUP_HEADERS
    )
    assert response.status_code == 409
    manager._running.discard(session.session_id)


def test_failed_import_rolls_back_new_workspace_and_task_records(tmp_path, monkeypatch):
    client, manager, _ = _studio(tmp_path, monkeypatch)
    project, session, root = _seed(client, manager)
    backup = client.get(
        f"/api/projects/{project['id']}/backup", headers=_BACKUP_HEADERS
    ).content
    original_sessions = {item["session_id"] for item in manager.list_sessions()}

    def fail_write(_project):
        raise OSError("disk full")

    monkeypatch.setattr(manager.projects, "_write", fail_write)
    response = client.post(
        "/api/projects/import", content=backup, headers=_BACKUP_HEADERS
    )
    assert response.status_code == 400
    assert {item["session_id"] for item in manager.list_sessions()} == original_sessions
    assert manager.projects.list() == [project]
    assert list(root.parent.iterdir()) == [root]
    assert (root / "方案.md").read_text(encoding="utf-8") == "新方案"
    assert manager.get_session(session.session_id).project_id == project["id"]


@pytest.mark.parametrize("bad_meta", [[], {"title": {}}, {"title": "x" * 65}])
def test_import_rejects_invalid_session_metadata_without_writing(
    tmp_path, monkeypatch, bad_meta
):
    client, manager, _ = _studio(tmp_path, monkeypatch)
    project, session, root = _seed(client, manager)
    backup = client.get(
        f"/api/projects/{project['id']}/backup", headers=_BACKUP_HEADERS
    ).content
    tampered = _replace_session_meta(backup, session.session_id, bad_meta)
    original_sessions = {item["session_id"] for item in manager.list_sessions()}

    response = client.post(
        "/api/projects/import", content=tampered, headers=_BACKUP_HEADERS
    )

    assert response.status_code == 400
    assert "元信息无效" in response.json()["detail"]
    assert manager.projects.list() == [project]
    assert {item["session_id"] for item in manager.list_sessions()} == original_sessions
    assert list(root.parent.iterdir()) == [root]


def test_project_backup_routes_require_access_token(tmp_path, monkeypatch):
    client, _, _ = _studio(tmp_path, monkeypatch, token="private-access")
    assert client.get(
        f"/api/projects/{'a' * 32}/backup", headers=_BACKUP_HEADERS
    ).status_code == 401
    assert client.post(
        "/api/projects/import", content=b"not-a-zip", headers=_BACKUP_HEADERS
    ).status_code == 401


def test_import_does_not_remove_directory_created_by_another_process(tmp_path, monkeypatch):
    client, manager, _ = _studio(tmp_path, monkeypatch)
    project, _, _ = _seed(client, manager)
    content = client.get(
        f"/api/projects/{project['id']}/backup", headers=_BACKUP_HEADERS
    ).content
    external = tmp_path / "managed" / "external-race"
    external.mkdir()
    marker = external / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(
        backup_module, "_new_name", lambda _name, _root: ("external", external)
    )

    response = client.post(
        "/api/projects/import", content=content, headers=_BACKUP_HEADERS
    )
    assert response.status_code == 400
    assert marker.read_text(encoding="utf-8") == "keep"


def test_portable_workspace_layout_rejects_case_and_unicode_collisions():
    with pytest.raises(ValueError, match="跨平台"):
        backup_module._validate_workspace_layout([], ["Report.md", "report.md"])
    with pytest.raises(ValueError, match="跨平台"):
        backup_module._validate_workspace_layout([], ["café.md", "cafe\u0301.md"])
    with pytest.raises(ValueError, match="同名"):
        backup_module._validate_workspace_layout(["assets"], ["ASSETS"])
    with pytest.raises(ValueError, match="路径层级"):
        backup_module._validate_workspace_layout(["report/assets"], ["report"])
    with pytest.raises(ValueError, match="不兼容"):
        backup_module._archive_path("workspace/report?.md")


def test_zip_preflight_rejects_entry_count_before_zipfile_allocates(tmp_path):
    backup = tmp_path / "oversized-directory.zip"
    backup.write_bytes(
        struct.pack(
            "<4s4H2LH",
            b"PK\x05\x06",
            0,
            0,
            backup_module._MAX_FILES + 2,
            backup_module._MAX_FILES + 2,
            0,
            0,
            0,
        )
    )
    with pytest.raises(ValueError, match="数量超过上限"):
        backup_module._preflight_zip(backup)


def test_zip_preflight_rejects_shifted_central_directory_count_bypass(tmp_path):
    actual_buffer = io.BytesIO()
    with zipfile.ZipFile(actual_buffer, "w") as archive:
        for index in range(3):
            archive.writestr(f"actual-{index}.txt", b"")
    actual_zip = actual_buffer.getvalue()
    eocd_offset = actual_zip.rfind(b"PK\x05\x06")
    eocd = list(struct.unpack_from("<4s4H2LH", actual_zip, eocd_offset))
    directory_size = eocd[5]
    actual_directory = actual_zip[eocd[6] : eocd[6] + directory_size]

    # The EOCD claims one entry at offset zero.  A fake one-entry directory at
    # that offset satisfies the old preflight, while ``zipfile`` applies its
    # prepended-data adjustment and parses the three-entry directory directly
    # before the EOCD instead.
    fake_buffer = io.BytesIO()
    fake_info = zipfile.ZipInfo("x")
    fake_info.extra = b"\0" * (directory_size - 47)
    with zipfile.ZipFile(fake_buffer, "w") as archive:
        archive.writestr(fake_info, b"")
    fake_zip = fake_buffer.getvalue()
    fake_eocd_offset = fake_zip.rfind(b"PK\x05\x06")
    fake_eocd = struct.unpack_from("<4s4H2LH", fake_zip, fake_eocd_offset)
    fake_directory = fake_zip[fake_eocd[6] : fake_eocd[6] + fake_eocd[5]]
    assert len(fake_directory) == directory_size

    eocd[3] = eocd[4] = 1
    eocd[6] = 0
    eocd[7] = 0
    backup = tmp_path / "shifted-directory.zip"
    backup.write_bytes(
        fake_directory + actual_directory + struct.pack("<4s4H2LH", *eocd)
    )
    with zipfile.ZipFile(backup) as archive:
        assert len(archive.infolist()) == 3

    with pytest.raises(ValueError, match="目录索引损坏"):
        backup_module._preflight_zip(backup)


def test_import_rejects_abnormal_compression_before_extracting(tmp_path, monkeypatch):
    client, manager, _ = _studio(tmp_path, monkeypatch)
    project, _, _ = _seed(client, manager)
    content = client.get(
        f"/api/projects/{project['id']}/backup", headers=_BACKUP_HEADERS
    ).content
    monkeypatch.setattr(backup_module, "_COMPRESSION_RATIO_THRESHOLD", 1)
    monkeypatch.setattr(backup_module, "_MAX_COMPRESSION_RATIO", 1)

    response = client.post(
        "/api/projects/import", content=content, headers=_BACKUP_HEADERS
    )

    assert response.status_code == 400
    assert "压缩比异常" in response.json()["detail"]
    assert manager.projects.list() == [project]


def test_import_checks_free_space_before_extracting(tmp_path, monkeypatch):
    client, manager, _ = _studio(tmp_path, monkeypatch)
    project, _, _ = _seed(client, manager)
    content = client.get(
        f"/api/projects/{project['id']}/backup", headers=_BACKUP_HEADERS
    ).content
    original_disk_usage = backup_module.shutil.disk_usage
    monkeypatch.setattr(
        backup_module.shutil,
        "disk_usage",
        lambda path: (
            type("DiskUsage", (), {"free": 0})()
            if Path(path) == Path(project["cwd"]).parent else original_disk_usage(path)
        ),
    )

    response = client.post(
        "/api/projects/import", content=content, headers=_BACKUP_HEADERS
    )

    assert response.status_code == 400
    assert "磁盘可用空间不足" in response.json()["detail"]
    assert manager.projects.list() == [project]


def _registered_stage(manager, root, *, suffix="abandoned"):
    stage = root / f"{backup_module._STAGE_PREFIX}{suffix}"
    stage.mkdir()
    token = uuid.uuid4().hex
    (stage / backup_module._STAGE_SENTINEL).write_text(token, encoding="utf-8")
    manager._session_dir.mkdir(parents=True, exist_ok=True)
    registration = manager._session_dir / f"{backup_module._STAGE_RECORD_PREFIX}{token}.json"
    registration.write_text(
        json.dumps({
            "version": 1,
            "owner_token": token,
            "stage_name": stage.name,
            "managed_root": str(root.resolve()),
            "process_id": backup_module.os.getpid(),
        }),
        encoding="utf-8",
    )
    return stage, registration


def test_interrupted_import_transaction_is_recovered(tmp_path, monkeypatch):
    _client, manager, _ = _studio(tmp_path, monkeypatch)
    root = tmp_path / "managed"
    root.mkdir()
    project_id = uuid.uuid4().hex
    transaction_id = uuid.uuid4().hex
    target = root / "未完成导入"
    target.mkdir()
    sentinel = target / f"{backup_module._TRANSACTION_SENTINEL}{transaction_id}"
    sentinel.write_text(transaction_id, encoding="utf-8")
    (target / "partial.txt").write_text("partial", encoding="utf-8")
    session = backup_module.Session.create(
        manager._session_dir, "test/model", target, system_prompt="system"
    )
    session.project_id = project_id
    session.save()
    run = manager._session_dir / "runs" / f"{session.session_id}.json"
    run.parent.mkdir(parents=True)
    run.write_text("{}", encoding="utf-8")
    change = manager._session_dir / "changes" / session.session_id / "change.json"
    change.parent.mkdir(parents=True)
    change.write_text("{}", encoding="utf-8")
    marker = backup_module._write_import_transaction(
        root, transaction_id, project_id, target, [session.session_id]
    )
    abandoned_stage, registration = _registered_stage(manager, root)
    (abandoned_stage / "partial.bin").write_bytes(b"partial")
    monkeypatch.setattr(backup_module, "_stage_process_is_active", lambda _payload: False)

    assert backup_module.recover_project_imports(manager, root) == []
    assert not marker.exists()
    assert not target.exists()
    assert not session.file_path.exists()
    assert not run.exists()
    assert not change.parent.exists()
    assert not abandoned_stage.exists()
    assert not registration.exists()


def test_recovery_preserves_forged_stage_and_active_registered_stage(tmp_path, monkeypatch):
    _client, manager, _ = _studio(tmp_path, monkeypatch)
    root = (tmp_path / "managed").resolve()
    root.mkdir()
    forged = root / f"{backup_module._STAGE_PREFIX}forged"
    forged.mkdir()
    (forged / backup_module._STAGE_SENTINEL).write_text(forged.name, encoding="utf-8")
    keep = forged / "keep.txt"
    keep.write_text("legitimate workspace", encoding="utf-8")

    with backup_module._owned_stage(manager, root) as active:
        assert backup_module.recover_project_imports(manager, root) == []
        assert active.is_dir()
        assert list(manager._session_dir.glob(f"{backup_module._STAGE_RECORD_PREFIX}*.json"))

    assert not active.exists()
    assert keep.read_text(encoding="utf-8") == "legitimate workspace"


def test_recovery_retains_stage_owned_by_another_live_process(tmp_path, monkeypatch):
    _client, manager, _ = _studio(tmp_path, monkeypatch)
    root = (tmp_path / "managed").resolve()
    root.mkdir()
    stage, registration = _registered_stage(manager, root)
    payload = json.loads(registration.read_text(encoding="utf-8"))
    worker = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        payload["process_id"] = worker.pid
        registration.write_text(json.dumps(payload), encoding="utf-8")
        assert backup_module.recover_project_imports(manager, root) == []
        assert worker.poll() is None
        assert stage.is_dir() and registration.is_file()
    finally:
        if worker.poll() is None:
            worker.terminate()
        worker.wait(timeout=5)


def test_recovery_retains_data_if_catalog_cannot_be_completely_read(tmp_path, monkeypatch):
    _client, manager, _ = _studio(tmp_path, monkeypatch)
    root = (tmp_path / "managed").resolve()
    root.mkdir()
    stage, registration = _registered_stage(manager, root)
    manager.projects.directory.mkdir(parents=True, exist_ok=True)
    (manager.projects.directory / f"{uuid.uuid4().hex}.json").write_text(
        "{broken", encoding="utf-8"
    )
    monkeypatch.setattr(backup_module, "_stage_process_is_active", lambda _payload: False)

    warnings = backup_module.recover_project_imports(manager, root)

    assert len(warnings) == 1 and "项目目录记录不完整" in warnings[0]["message"]
    assert stage.is_dir() and registration.is_file()


@pytest.mark.parametrize("kind", ["stage", "transaction"])
@pytest.mark.parametrize("relationship", ["same", "ancestor", "descendant"])
def test_recovery_protects_all_catalogued_workspace_relationships(
    tmp_path, monkeypatch, kind, relationship
):
    _client, manager, _ = _studio(tmp_path, monkeypatch)
    root = (tmp_path / "managed").resolve()
    root.mkdir()
    if kind == "stage":
        target, marker = _registered_stage(manager, root)
        monkeypatch.setattr(backup_module, "_stage_process_is_active", lambda _payload: False)
    else:
        target = root / "unfinished-import"
        target.mkdir()
        transaction_id = uuid.uuid4().hex
        (target / f"{backup_module._TRANSACTION_SENTINEL}{transaction_id}").write_text(
            transaction_id, encoding="utf-8"
        )
        marker = backup_module._write_import_transaction(
            root, transaction_id, uuid.uuid4().hex, target, []
        )
    workspace = {
        "same": target,
        "ancestor": root,
        "descendant": target / "nested-workspace",
    }[relationship]
    workspace.mkdir(parents=True, exist_ok=True)
    keep = workspace / "keep.txt"
    keep.write_text("user data", encoding="utf-8")
    manager.projects._write(backup_module.ProjectRecord(
        id=uuid.uuid4().hex, name="Existing project", cwd=str(workspace), created_at="now"
    ).model_dump())

    warnings = backup_module.recover_project_imports(manager, root)

    assert len(warnings) == 1 and "已有项目" in warnings[0]["message"]
    assert marker.exists()
    assert target.exists()
    assert keep.read_text(encoding="utf-8") == "user data"


@pytest.mark.parametrize("record_kind", ["session", "change", "run"])
def test_import_file_creation_race_preserves_foreign_record(
    tmp_path, monkeypatch, record_kind
):
    client, manager, _ = _studio(tmp_path, monkeypatch)
    project, session, root = _seed(client, manager)
    run = manager._session_dir / "runs" / f"{session.session_id}.json"
    run.parent.mkdir(parents=True, exist_ok=True)
    run.write_text("{}", encoding="utf-8")
    content = client.get(
        f"/api/projects/{project['id']}/backup", headers=_BACKUP_HEADERS
    ).content
    original_copy = backup_module._copy_import_file
    collisions = []

    def create_collision(source, destination, created_files):
        matches = (
            record_kind == "session" and destination.suffix == ".jsonl"
            or record_kind == "change" and destination.parent.parent.name == "changes"
            or record_kind == "run" and destination.parent.name == "runs"
        )
        if matches and not collisions:
            destination.write_bytes(b"other-process-data")
            collisions.append(destination)
        original_copy(source, destination, created_files)

    monkeypatch.setattr(backup_module, "_copy_import_file", create_collision)
    response = client.post(
        "/api/projects/import", content=content, headers=_BACKUP_HEADERS
    )

    assert response.status_code == 400
    assert len(collisions) == 1
    assert collisions[0].read_bytes() == b"other-process-data"
    backup_module.recover_project_imports(manager, root.parent)
    assert collisions[0].read_bytes() == b"other-process-data"
    assert manager.projects.list() == [project]


def test_backup_routes_require_explicit_browser_intent(tmp_path, monkeypatch):
    client, manager, _ = _studio(tmp_path, monkeypatch)
    project, _, _ = _seed(client, manager)
    assert client.get(f"/api/projects/{project['id']}/backup").status_code == 403
    assert client.post(
        "/api/projects/import",
        content=b"not-a-zip",
        headers={"Content-Type": "application/zip"},
    ).status_code == 403
    assert client.post(
        "/api/projects/import",
        content=b"not-a-zip",
        headers={"X-Jelly-Intent": "project-backup"},
    ).status_code == 415
    assert client.post(
        "/api/projects/import",
        content=b"x",
        headers={
            **_BACKUP_HEADERS,
            "Content-Length": str(backup_module.MAX_BACKUP_UPLOAD_BYTES + 1),
        },
    ).status_code == 413


def test_backup_ticket_requires_auth_and_intent_then_streams_once(
    tmp_path, monkeypatch
):
    created: list[Path] = []
    original_create = project_routes_module.create_project_backup

    def capture_backup(*args, **kwargs):
        path = original_create(*args, **kwargs)
        created.append(path)
        return path

    monkeypatch.setattr(project_routes_module, "create_project_backup", capture_backup)
    client, _, _ = _studio(tmp_path, monkeypatch, token="private-access")
    auth = {"Authorization": "Bearer private-access"}
    project_response = client.post(
        "/api/projects/managed",
        json={"name": "可迁移项目", "permission": "ask"},
        headers=auth,
    )
    assert project_response.status_code == 200, project_response.text
    project_id = project_response.json()["id"]
    endpoint = f"/api/projects/{project_id}/backup-ticket"

    assert client.post(endpoint, headers=_BACKUP_HEADERS).status_code == 401
    assert client.post(endpoint, headers=auth).status_code == 403
    ticket_response = client.post(
        endpoint,
        headers={**auth, "X-Jelly-Intent": "project-backup"},
    )
    assert ticket_response.status_code == 200, ticket_response.text
    body = ticket_response.json()
    assert body["expires_in"] in range(1, 61)
    assert body["url"].startswith("/api/project-backups/")
    assert len(body["url"].rsplit("/", 1)[1]) >= 40
    assert created[0].is_file()

    download = client.get(body["url"])
    assert download.status_code == 200, download.text
    assert download.headers["content-type"].startswith("application/zip")
    assert download.headers["cache-control"] == "no-store"
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert json.loads(archive.read("manifest.json"))["format"] == "jelly-project-backup"
    assert not created[0].exists()
    assert client.get(body["url"]).status_code == 404


def test_expired_backup_ticket_is_rejected_and_archive_is_removed(
    tmp_path, monkeypatch
):
    created: list[Path] = []
    original_create = project_routes_module.create_project_backup

    def capture_backup(*args, **kwargs):
        path = original_create(*args, **kwargs)
        created.append(path)
        return path

    monkeypatch.setattr(project_routes_module, "create_project_backup", capture_backup)
    monkeypatch.setattr(project_routes_module, "_BACKUP_TICKET_TTL_SECONDS", -1.0)
    client, manager, _ = _studio(tmp_path, monkeypatch)
    project, _, _ = _seed(client, manager)

    ticket_response = client.post(
        f"/api/projects/{project['id']}/backup-ticket",
        headers={"X-Jelly-Intent": "project-backup"},
    )
    assert ticket_response.status_code == 200, ticket_response.text
    assert client.get(ticket_response.json()["url"]).status_code == 404
    assert created and not created[0].exists()


def test_import_rejects_full_temporary_disk_before_allocating_upload(tmp_path, monkeypatch):
    client, manager, _ = _studio(tmp_path, monkeypatch)
    disk = backup_module.shutil.disk_usage(tmp_path)
    monkeypatch.setattr(
        project_routes_module.shutil, "disk_usage", lambda _path: disk._replace(free=0)
    )
    monkeypatch.setattr(
        project_routes_module.tempfile, "mkstemp",
        lambda **_: pytest.fail("must not allocate an upload on a full temporary disk"),
    )
    response = client.post(
        "/api/projects/import", content=b"backup", headers=_BACKUP_HEADERS
    )
    assert response.status_code == 400
    assert "临时磁盘空间不足" in response.json()["detail"]
    assert manager.projects.list() == []
    assert client.get("/api/projects").json()["backup_busy"] is False
