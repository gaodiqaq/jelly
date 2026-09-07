import pytest

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
