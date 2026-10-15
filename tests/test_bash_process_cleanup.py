"""Bash timeouts must release inherited pipes and stop only their own process tree."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest

from agent_shell.tools.base import ToolContext
from agent_shell.tools.bash import BashArgs, run_bash


def _command(arguments: list[str]) -> str:
    return subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)


def _running(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        handle = api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return api.WaitForSingleObject(handle, 0) == 0x00000102  # WAIT_TIMEOUT
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A terminated orphan may briefly await reaping by the OS. It cannot execute
    # or retain output handles, and should not count as a surviving subprocess.
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


def _kill_pid(pid: int) -> None:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        api.TerminateProcess.restype = wintypes.BOOL
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        handle = api.OpenProcess(0x0001, False, pid)
        if handle:
            try:
                api.TerminateProcess(handle, 1)
            finally:
                api.CloseHandle(handle)
    else:
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize("parent_exits", [False, True], ids=["waiting-parent", "exited-shell"])
def test_timeout_stops_immediate_descendant_and_releases_pipes(
    tmp_path: Path, parent_exits: bool
) -> None:
    (tmp_path / "child.py").write_text(
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "Path('child.pid').write_text(str(os.getpid()))\n"
        "sys.stdout.buffer.write('child-ready · 子进程已启动\\n'.encode('utf-8'))\n"
        "sys.stdout.flush()\n"
        "sys.stderr.buffer.write('超时前的诊断\\n'.encode('utf-8'))\n"
        "sys.stderr.flush()\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    (tmp_path / "parent.py").write_text(
        "import os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "Path('parent.pid').write_text(str(os.getpid()))\n"
        "subprocess.Popen([sys.executable, 'child.py'])\n"
        + ("" if parent_exits else "time.sleep(30)\n"),
        encoding="utf-8",
    )
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        started = time.monotonic()
        result = run_bash(
            ToolContext(cwd=tmp_path),
            BashArgs(command=_command([sys.executable, "parent.py"]), timeout=1),
        )
        elapsed = time.monotonic() - started
        assert result.is_error and "超时" in result.content
        assert "child-ready" in result.content
        assert "子进程已启动" in result.content and "超时前的诊断" in result.content
        assert elapsed < 8, f"A one-second timeout waited {elapsed:.1f}s for a descendant"
        for marker in ("parent.pid", "child.pid"):
            pid = int((tmp_path / marker).read_text())
            deadline = time.monotonic() + 3
            while _running(pid) and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not _running(pid), f"{marker} survived the command timeout"
        assert unrelated.poll() is None, "Cleanup terminated an unrelated process"
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)
        for marker in ("parent.pid", "child.pid"):
            if (tmp_path / marker).exists():
                pid = int((tmp_path / marker).read_text())
                if _running(pid):
                    _kill_pid(pid)


def test_successful_command_can_leave_an_intentional_background_process(tmp_path: Path) -> None:
    (tmp_path / "background.py").write_text(
        "import subprocess, sys\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],\n"
        "    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)\n"
        "Path('background.pid').write_text(str(child.pid))\n",
        encoding="utf-8",
    )
    try:
        result = run_bash(
            ToolContext(cwd=tmp_path),
            BashArgs(command=_command([sys.executable, "background.py"]), timeout=5),
        )
        assert not result.is_error, result.content
        pid = int((tmp_path / "background.pid").read_text())
        assert _running(pid), "Normal completion must preserve intentional background services"
    finally:
        if (tmp_path / "background.pid").exists():
            _kill_pid(int((tmp_path / "background.pid").read_text()))
