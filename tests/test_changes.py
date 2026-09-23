import pytest

from agent_shell.core import changes as changes_module
from agent_shell.core.changes import ChangeJournal
from agent_shell.core.executor import ToolExecutor
from agent_shell.tools import build_registry
from agent_shell.types import ToolCall


def executor(root, permission="auto"):
    journal = ChangeJournal(root / "snapshots", root)
    return ToolExecutor(
        build_registry(cwd=root), None, default_permission=permission, journal=journal
    ), journal


def test_restore_preserves_original_bytes_and_survives_reload(tmp_path):
    file = tmp_path / "work.md"
    file.write_bytes(b"before\r\n")
    runner, journal = executor(tmp_path)
    result = runner.execute(
        ToolCall(
            id="a",
            name="write",
            arguments={"path": "work.md", "content": "after", "overwrite": True},
        )
    )
    assert not result.is_error
    journal = ChangeJournal(journal.directory, tmp_path)
    change = journal.list()[0]
    assert "-before" in change["diff"] and "+after" in change["diff"]
    journal.restore(change["id"])
    assert file.read_bytes() == b"before\r\n"
    assert journal.list()[0]["state"] == "restored"


def test_restore_refuses_later_user_edits(tmp_path):
    runner, journal = executor(tmp_path)
    runner.execute(ToolCall(id="a", name="write", arguments={"path": "new.md", "content": "agent"}))
    (tmp_path / "new.md").write_text("user")
    with pytest.raises(ValueError, match="后续修改"):
        journal.restore(journal.list()[0]["id"])
    assert (tmp_path / "new.md").read_text() == "user"


def test_restore_new_file_and_refuse_repeat(tmp_path):
    runner, journal = executor(tmp_path)
    runner.execute(ToolCall(id="a", name="write", arguments={"path": "new.md", "content": "agent"}))
    change = journal.list()[0]
    journal.restore(change["id"])
    assert not (tmp_path / "new.md").exists()
    with pytest.raises(ValueError):
        journal.restore(change["id"])


def test_denied_write_does_not_snapshot(tmp_path):
    runner, journal = executor(tmp_path, "readonly")
    assert runner.execute(
        ToolCall(id="a", name="write", arguments={"path": "new.md", "content": "agent"})
    ).is_error
    assert journal.list() == []


def test_outside_workspace_refused(tmp_path):
    runner, journal = executor(tmp_path)
    outside = tmp_path.parent / "outside.md"
    assert runner.execute(
        ToolCall(id="a", name="write", arguments={"path": str(outside), "content": "agent"})
    ).is_error
    assert not outside.exists()
    with pytest.raises(ValueError):
        journal.restore("../../bad")


def test_large_file_refused_without_overwrite(tmp_path):
    (tmp_path / "large").write_bytes(b"x" * 2_000_001)
    runner, _ = executor(tmp_path)
    assert runner.execute(
        ToolCall(
            id="a", name="write", arguments={"path": "large", "content": "small", "overwrite": True}
        )
    ).is_error
    assert (tmp_path / "large").stat().st_size == 2_000_001


def test_large_new_file_reports_snapshot_warning_without_retryable_failure(tmp_path):
    runner, journal = executor(tmp_path)
    content = "x" * 2_000_001

    result = runner.execute(
        ToolCall(id="a", name="write", arguments={"path": "large-new", "content": content})
    )

    assert not result.is_error
    assert "文件已处理" in result.content
    assert (tmp_path / "large-new").stat().st_size == len(content)
    change = journal.list()[0]
    assert change["state"] == "snapshot_failed"
    assert change["diff"] == ""


def test_corrupt_record_is_isolated_from_valid_versions(tmp_path):
    runner, journal = executor(tmp_path)
    runner.execute(ToolCall(id="a", name="write", arguments={"path": "good.md", "content": "ok"}))
    valid = journal.list()[0]
    corrupt = journal.directory / ("b" * 32 + ".json")
    corrupt.write_text("{broken", encoding="utf-8")

    assert [item["id"] for item in journal.list()] == [valid["id"]]
    assert journal.issues() == [{"file": corrupt.name, "message": "版本记录损坏，已隔离"}]
    with pytest.raises(ValueError, match="已损坏"):
        journal.restore(corrupt.stem)
    assert journal.restore(valid["id"])["restored"]


def test_failed_atomic_journal_save_keeps_previous_record(tmp_path, monkeypatch):
    runner, journal = executor(tmp_path)
    runner.execute(ToolCall(id="a", name="write", arguments={"path": "good.md", "content": "ok"}))
    change = journal.list()[0]
    record_path = journal.directory / f"{change['id']}.json"
    original = record_path.read_bytes()
    record = journal._load(record_path)
    record["state"] = "restoring"

    def fail_replace(*_args):
        raise OSError("replace failed")

    monkeypatch.setattr(changes_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        journal._save(record)
    assert record_path.read_bytes() == original
    assert list(journal.directory.glob("*.tmp")) == []


def test_restore_preflight_failure_does_not_change_file(tmp_path, monkeypatch):
    runner, journal = executor(tmp_path)
    file = tmp_path / "good.md"
    file.write_text("before", encoding="utf-8")
    runner.execute(
        ToolCall(
            id="a", name="write",
            arguments={"path": "good.md", "content": "after", "overwrite": True},
        )
    )
    change = journal.list()[0]

    def fail_save(_record):
        raise OSError("disk full")

    monkeypatch.setattr(journal, "_save", fail_save)
    with pytest.raises(OSError, match="disk full"):
        journal.restore(change["id"])
    assert file.read_text(encoding="utf-8") == "after"


def test_restore_commit_failure_reports_success_and_reconciles(tmp_path, monkeypatch):
    runner, journal = executor(tmp_path)
    file = tmp_path / "good.md"
    file.write_text("before", encoding="utf-8")
    runner.execute(
        ToolCall(
            id="a", name="write",
            arguments={"path": "good.md", "content": "after", "overwrite": True},
        )
    )
    change = journal.list()[0]
    save = journal._save
    calls = 0

    def fail_second_save(record):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        save(record)

    monkeypatch.setattr(journal, "_save", fail_second_save)
    result = journal.restore(change["id"])
    assert result["restored"] and "文件已恢复" in result["warning"]
    assert file.read_text(encoding="utf-8") == "before"
    reloaded = ChangeJournal(journal.directory, tmp_path)
    assert reloaded.list()[0]["state"] == "restored"
    assert reloaded.issues() == []
