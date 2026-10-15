"""Workspace backups never reset trust when a directory is replaced mid-export."""

from __future__ import annotations

import os
import stat
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_shell.server import project_backup as backup_module


def _directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, check=False,
        )
        if result.returncode:
            pytest.skip("Directory junction creation is unavailable")
    else:
        link.symlink_to(target, target_is_directory=True)


def _manager(root: Path):
    project = {"id": "project", "name": "Project", "cwd": str(root)}
    return SimpleNamespace(
        _lifecycle_lock=threading.RLock(),
        projects=SimpleNamespace(get=lambda _id: project),
        _running=set(),
    )


@pytest.mark.parametrize("replace_root", [False, True])
def test_export_rejects_parent_redirected_after_enumeration(
    tmp_path, monkeypatch, replace_root,
):
    root = tmp_path / "workspace"
    inside = root / "nested"
    inside.mkdir(parents=True)
    (inside / "result.txt").write_bytes(b"workspace contents")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_parent = outside / "nested" if replace_root else outside
    outside_parent.mkdir(exist_ok=True)
    secret = outside_parent / "result.txt"
    secret.write_bytes(b"external secret contents")
    replaced = root if replace_root else inside
    retained = tmp_path / "retained"
    original_entries = backup_module._workspace_entries

    def redirected_entries(path):
        result = original_entries(path)
        replaced.rename(retained)
        _directory_link(replaced, outside)
        return result

    monkeypatch.setattr(backup_module, "_workspace_entries", redirected_entries)
    monkeypatch.setattr(backup_module, "_metadata_entries", lambda *_args: [])
    try:
        with pytest.raises(ValueError, match="备份期间发生变化"):
            backup_module.create_project_backup(_manager(root), "project")
        assert secret.read_bytes() == b"external secret contents"
        original = retained / ("nested/result.txt" if replace_root else "result.txt")
        assert original.read_bytes() == b"workspace contents"
    finally:
        if replaced.is_symlink() or (replaced.exists() and backup_module._is_link_like(replaced)):
            # rmdir removes only the junction itself on Windows.
            if os.name == "nt":
                replaced.rmdir()
            else:
                replaced.unlink()


def test_export_rejects_file_replaced_after_enumeration(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "result.txt"
    target.write_bytes(b"workspace contents")
    original_info = target.stat()
    external = tmp_path / "replacement.txt"
    external.write_bytes(b"external secret!!!")
    assert external.stat().st_size == original_info.st_size
    os.utime(external, ns=(original_info.st_atime_ns, original_info.st_mtime_ns))
    original_entries = backup_module._workspace_entries

    def replaced_entries(path):
        result = original_entries(path)
        target.rename(root / "original.txt")
        external.rename(target)
        return result

    monkeypatch.setattr(backup_module, "_workspace_entries", replaced_entries)
    monkeypatch.setattr(backup_module, "_metadata_entries", lambda *_args: [])
    with pytest.raises(ValueError, match="文件在备份期间发生变化"):
        backup_module.create_project_backup(_manager(root), "project")


def test_open_workspace_source_cannot_follow_a_concurrent_parent_redirect(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    parent = root / "nested"
    parent.mkdir(parents=True)
    target = parent / "result.txt"
    target.write_bytes(b"workspace contents")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "result.txt").write_bytes(b"external secret contents")
    retained = tmp_path / "retained"
    _directories, files = backup_module._workspace_entries(root)
    attempted = False
    blocked = False

    def redirect_parent():
        nonlocal attempted, blocked
        attempted = True
        try:
            parent.rename(retained)
        except PermissionError:
            blocked = True
            return
        _directory_link(parent, outside)

    if os.name == "nt":
        original_open = backup_module._windows_source_fd

        def source_open(path):
            if path == target:
                redirect_parent()
            return original_open(path)

        monkeypatch.setattr(backup_module, "_windows_source_fd", source_open)
    else:
        original_open = os.open

        def descriptor_open(path, flags, *args, **kwargs):
            if path == target.name and kwargs.get("dir_fd") is not None:
                redirect_parent()
            return original_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", descriptor_open)

    def read_during_attack():
        with backup_module._backup_source(target, root, files) as stream:
            assert stream.read() == b"workspace contents"

    try:
        if os.name == "nt":
            read_during_attack()
            assert blocked
        else:
            with pytest.raises(ValueError, match="备份期间发生变化"):
                read_during_attack()
        assert attempted
    finally:
        if parent.is_symlink() or (parent.exists() and backup_module._is_link_like(parent)):
            if os.name == "nt":
                parent.rmdir()
            else:
                parent.unlink()


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO regression")
def test_file_replaced_by_fifo_at_open_is_rejected_without_waiting(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "result.txt"
    target.write_bytes(b"workspace contents")
    _directories, files = backup_module._workspace_entries(root)
    original_open = os.open
    attempted = False

    def descriptor_open(path, flags, *args, **kwargs):
        nonlocal attempted
        if path == target.name and kwargs.get("dir_fd") is not None:
            # Fail before opening the FIFO if a regression would block pytest.
            assert flags & os.O_NONBLOCK
            assert flags & os.O_NOFOLLOW
            target.unlink()
            os.mkfifo(target)
            attempted = True
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", descriptor_open)
    with (
        pytest.raises(ValueError, match="文件在备份期间发生变化"),
        backup_module._backup_source(target, root, files),
    ):
        pytest.fail("A FIFO must be rejected before yielding a readable source")
    assert attempted


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point regression")
def test_file_replaced_by_symlink_at_open_is_not_followed(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "result.txt"
    target.write_bytes(b"workspace contents")
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"external secret contents")
    probe = tmp_path / "probe-link"
    try:
        probe.symlink_to(outside)
    except OSError:
        pytest.skip("Windows file symlink creation requires Developer Mode or privilege")
    probe.unlink()
    _directories, files = backup_module._workspace_entries(root)
    original_open = backup_module._windows_source_fd
    attempted = False

    def source_open(path):
        nonlocal attempted
        if path == target:
            target.unlink()
            target.symlink_to(outside)
            attempted = True
        descriptor = original_open(path)
        info = os.fstat(descriptor)
        assert backup_module._stat_is_link_like(info)
        assert (info.st_dev, info.st_ino) != (outside.stat().st_dev, outside.stat().st_ino)
        return descriptor

    monkeypatch.setattr(backup_module, "_windows_source_fd", source_open)
    with (
        pytest.raises(ValueError, match="文件在备份期间发生变化"),
        backup_module._backup_source(target, root, files),
    ):
        pytest.fail("A symlink must be rejected before yielding its external target")
    assert attempted


@pytest.mark.skipif(os.name != "nt", reason="Windows CreateFile policy boundary")
def test_windows_file_open_passes_no_follow_flag_to_createfile(tmp_path, monkeypatch):
    import ctypes

    target = tmp_path / "result.txt"
    target.write_bytes(b"workspace contents")
    original_dll = ctypes.WinDLL
    kernel32 = original_dll("kernel32", use_last_error=True)
    real_create = kernel32.CreateFileW
    captured = []

    class CaptureCreateFile:
        def __call__(self, *args):
            real_create.argtypes = self.argtypes
            real_create.restype = self.restype
            captured.append(args)
            return real_create(*args)

    monkeypatch.setattr(
        ctypes, "WinDLL", lambda *_args, **_kwargs: SimpleNamespace(
            CreateFileW=CaptureCreateFile(), CloseHandle=kernel32.CloseHandle,
        ),
    )
    descriptor = backup_module._windows_source_fd(target)
    try:
        assert stat.S_ISREG(os.fstat(descriptor).st_mode)
        assert len(captured) == 1
        assert captured[0][5] & 0x00200000  # FILE_FLAG_OPEN_REPARSE_POINT
    finally:
        os.close(descriptor)
