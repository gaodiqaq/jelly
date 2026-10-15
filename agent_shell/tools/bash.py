"""bash 工具：在本地 shell 中执行命令并返回输出。"""

from __future__ import annotations

import os
import signal
import subprocess
from contextlib import suppress
from typing import Any

from pydantic import BaseModel, Field

from agent_shell.tools.base import ToolContext, ToolRegistry
from agent_shell.types import ToolResult, ToolSpec

MAX_OUTPUT_MARK = "\n...[输出已截断]..."
_SECRET_ENV_MARKERS = (
    "API_KEY",
    "ACCESS_KEY",
    "AUTH",
    "COOKIE",
    "CREDENTIAL",
    "DATABASE_URL",
    "DSN",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
    "SESSION",
    "TOKEN",
    "WEB_USERS",
)


def _safe_environment() -> dict[str, str]:
    """Build a child environment without credentials owned by the Jelly service."""
    return {
        name: value
        for name, value in os.environ.items()
        if not any(marker in name.upper() for marker in _SECRET_ENV_MARKERS)
    }


class BashArgs(BaseModel):
    """bash 工具参数。

    Attributes:
        command: 要执行的 shell 命令。
        timeout: 超时秒数（None 使用上下文默认值）。
    """

    command: str = Field(description="要执行的 shell 命令")
    timeout: float | None = Field(
        default=None, ge=1, le=3600, description="超时秒数，默认使用全局配置"
    )


def _shell_command(command: str) -> list[str]:
    """构造跨平台 shell 调用命令。

    Windows 使用 ``cmd /c``，其他平台使用 ``/bin/bash -lc``。

    Args:
        command: 用户命令。

    Returns:
        argv 列表。
    """
    if os.name == "nt":
        return ["cmd.exe", "/c", command]
    return ["/bin/bash", "-lc", command]


def _truncate(text: str, max_chars: int) -> str:
    """按字符数截断文本，超限时附加截断标记。

    Args:
        text: 原始输出。
        max_chars: 上限。

    Returns:
        截断后的文本。
    """
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + MAX_OUTPUT_MARK


class _WindowsJob:
    """Bind a suspended command to a job before it can spawn any descendants."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class ThreadEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG),
                ("tpDeltaPri", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
            ]

        self.ctypes = ctypes
        self.thread_entry = ThreadEntry
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        declarations = {
            "CreateJobObjectW": ([wintypes.LPVOID, wintypes.LPCWSTR], wintypes.HANDLE),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            "Thread32First": ([wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
            "Thread32Next": ([wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
            "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in declarations.items():
            function = getattr(self.api, name)
            function.argtypes = arguments
            function.restype = result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def start(self, process: subprocess.Popen[str]) -> None:
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE are required for job assignment.
        handle = self.api.OpenProcess(0x0100 | 0x0001, False, process.pid)
        if not handle:
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle, handle):
                raise self.ctypes.WinError(self.ctypes.get_last_error())
        finally:
            self.api.CloseHandle(handle)

        # Popen closes the primary thread handle. Find and resume that thread only
        # after job assignment; assigning an already-running shell has a spawn race.
        snapshot = self.api.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
        if snapshot == self.ctypes.c_void_p(-1).value:
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        resumed = False
        try:
            entry = self.thread_entry()
            entry.dwSize = self.ctypes.sizeof(entry)
            available = self.api.Thread32First(snapshot, self.ctypes.byref(entry))
            while available:
                if entry.th32OwnerProcessID == process.pid:
                    thread = self.api.OpenThread(0x0002, False, entry.th32ThreadID)
                    if thread:
                        try:
                            count = self.api.ResumeThread(thread)
                            resumed = resumed or 0 < count < 0xFFFFFFFF
                        finally:
                            self.api.CloseHandle(thread)
                available = self.api.Thread32Next(snapshot, self.ctypes.byref(entry))
        finally:
            self.api.CloseHandle(snapshot)
        if not resumed:
            raise OSError("无法恢复命令进程的主线程")

    def terminate(self) -> None:
        if not self.api.TerminateJobObject(self.handle, 1):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def close(self) -> None:
        # Successful commands may deliberately start background services. Closing
        # this job handle must not terminate those services.
        self.api.CloseHandle(self.handle)


def _terminate_command(process: subprocess.Popen[str], job: _WindowsJob | None) -> None:
    try:
        if job is not None:
            job.terminate()
        else:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
    finally:
        # Startup can fail before the suspended Windows process is assigned.
        if process.poll() is None:
            process.kill()


def run_bash(ctx: ToolContext, args: BashArgs) -> ToolResult:
    """执行 bash 命令并捕获输出。

    Args:
        ctx: 工具执行上下文（提供 cwd 与输出上限）。
        args: 命令与超时参数。

    Returns:
        ToolResult: 退出码非 0 时 ``is_error=True``，content 包含 stdout 与 stderr。
    """
    timeout = args.timeout if args.timeout is not None else ctx.bash_timeout
    process = None
    job = None
    communication_finished = False
    try:
        options: dict[str, Any] = {"start_new_session": True}
        if os.name == "nt":
            job = _WindowsJob()
            # CREATE_SUSPENDED prevents cmd.exe from creating children before
            # its job assignment. CREATE_NEW_PROCESS_GROUP isolates console signals.
            options = {"creationflags": 0x00000004 | subprocess.CREATE_NEW_PROCESS_GROUP}
        process = subprocess.Popen(
            _shell_command(args.command),
            cwd=str(ctx.cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_safe_environment(),
            **options,
        )
        if job is not None:
            job.start(process)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            communication_finished = True
        except subprocess.TimeoutExpired:
            _terminate_command(process, job)
            stdout, stderr = process.communicate()
            communication_finished = True
            partial = (stdout or "") + (f"\n[stderr]\n{stderr}" if stderr else "")
            return ToolResult(
                content=(
                    f"命令超时（>{timeout}s）: {args.command}\n"
                    f"已输出:\n{_truncate(partial, ctx.max_output_chars)}"
                ),
                is_error=True,
            )
    except OSError as exc:
        return ToolResult(
            content=f"无法启动 shell: {exc}",
            is_error=True,
        )
    finally:
        try:
            if process is not None:
                try:
                    if not communication_finished:
                        _terminate_command(process, job)
                    process.wait()
                finally:
                    if process.stdout is not None:
                        process.stdout.close()
                    if process.stderr is not None:
                        process.stderr.close()
        finally:
            if job is not None:
                job.close()

    stdout = stdout or ""
    stderr = stderr or ""
    stderr_block = f"\n[stderr]\n{stderr}" if stderr else ""
    content = f"$ {args.command}\n{stdout}{stderr_block}".rstrip()
    if process.returncode != 0:
        content += f"\n[退出码: {process.returncode}]"
    return ToolResult(
        content=_truncate(content, ctx.max_output_chars),
        is_error=process.returncode != 0,
    )


def build_bash_spec() -> tuple[ToolSpec, type[BashArgs], Any]:
    """构建 bash 工具声明与实现。

    Returns:
        (spec, args_model, handler) 三元组。
    """
    spec = ToolSpec(
        name="bash",
        description=(
            "在本地终端执行 shell 命令。适用于文件操作、运行程序、git 操作、"
            "系统查询等任何无法用专用工具完成的任务。命令在当前工作目录执行，"
            "输出会被截断。修改性命令需要用户审批。"
        ),
        parameters=BashArgs.model_json_schema(),
        read_only=False,
    )
    return spec, BashArgs, run_bash


def register_bash(registry: ToolRegistry) -> None:
    """将 bash 工具注册到注册表。

    Args:
        registry: 目标注册表。
    """
    spec, args_model, handler = build_bash_spec()
    registry.register(spec, args_model, handler)
