"""搜索工具：glob / grep。"""

from __future__ import annotations

import fnmatch
import glob as glob_module
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent_shell.tools.base import ToolContext, ToolRegistry
from agent_shell.types import ToolResult, ToolSpec

DEFAULT_EXCLUDE_DIRS = (
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".idea",
    ".vscode",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
)

MAX_OUTPUT_MARK = "\n...[结果已截断]..."


def _truncate(text: str, max_chars: int) -> str:
    """按字符数截断文本。

    Args:
        text: 原始文本。
        max_chars: 上限。

    Returns:
        截断后的文本（超限时附加标记）。
    """
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + MAX_OUTPUT_MARK


class GlobArgs(BaseModel):
    """glob 工具参数。"""

    pattern: str = Field(description="glob 模式，如 '**/*.py' 或 'src/**/*.ts'")
    path: str = Field(default=".", description="搜索根目录")


class GrepArgs(BaseModel):
    """grep 工具参数。"""

    pattern: str = Field(description="正则表达式")
    path: str = Field(default=".", description="搜索根目录（文件或目录）")
    include: str | None = Field(
        default=None, description="文件名通配符过滤，如 '*.py'（fnmatch 语法）"
    )
    case_insensitive: bool = Field(default=False, description="忽略大小写")
    max_results: int = Field(default=200, ge=1, le=5000, description="最大匹配行数")


def _to_posix(path: Path) -> str:
    """将路径转换为正斜杠格式（对模型输出友好，跨平台一致）。

    Args:
        path: 路径对象。

    Returns:
        正斜杠路径字符串。
    """
    return path.as_posix()


def glob_files(ctx: ToolContext, args: GlobArgs) -> ToolResult:
    """按 glob 模式查找文件。

    Args:
        ctx: 工具上下文。
        args: 模式与根目录。

    Returns:
        ToolResult: 匹配的相对路径列表；根目录不存在时 ``is_error=True``。
    """
    root = _resolve_dir(ctx, args.path)
    if root is None:
        return ToolResult(content=f"目录不存在: {args.path}", is_error=True)
    pattern = str(root / args.pattern)
    try:
        matches = glob_module.glob(pattern, recursive=True)
    except OSError as exc:
        return ToolResult(content=f"glob 搜索失败: {exc}", is_error=True)
    results = sorted(_to_posix(Path(m).relative_to(root)) for m in matches if Path(m).is_file())
    content = f"匹配 {len(results)} 个文件（根目录 {root}）:\n" + "\n".join(results[:500])
    if len(results) > 500:
        content += MAX_OUTPUT_MARK
    return ToolResult(content=_truncate(content, ctx.max_output_chars))


def _resolve_dir(ctx: ToolContext, path: str) -> Path | None:
    """解析目录路径并验证存在。

    Args:
        ctx: 工具上下文。
        path: 用户路径。

    Returns:
        规范化目录；不存在时返回 None。
    """
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = ctx.cwd / p
    p = p.resolve()
    return p if p.is_dir() else None


def _iter_files(root: Path, include: str | None) -> list[Path]:
    """遍历目录下的文本文件（跳过常见忽略目录与二进制文件）。

    Args:
        root: 搜索根目录。
        include: 文件名通配符过滤。

    Returns:
        文件路径列表。
    """
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if d not in DEFAULT_EXCLUDE_DIRS and not d.startswith(".")
        ]
        for name in filenames:
            if include is not None and not fnmatch.fnmatch(name, include):
                continue
            files.append(Path(dirpath) / name)
    return sorted(files)


def _is_binary_file(path: Path) -> bool:
    """快速检测文件是否含 NUL 字节（二进制）。

    Args:
        path: 文件路径。

    Returns:
        是否二进制文件。
    """
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(4096)
    except OSError:
        return True


def grep_files(ctx: ToolContext, args: GrepArgs) -> ToolResult:
    """在文件中按正则表达式搜索。

    Args:
        ctx: 工具上下文。
        args: 模式、根目录与过滤参数。

    Returns:
        ToolResult: 匹配行（path:line: 内容）；正则非法时 ``is_error=True``。
    """
    root = _resolve_dir(ctx, args.path)
    if root is None:
        return ToolResult(content=f"目录不存在: {args.path}", is_error=True)
    try:
        flags = re.IGNORECASE if args.case_insensitive else 0
        regex = re.compile(args.pattern, flags)
    except re.error as exc:
        return ToolResult(content=f"正则表达式非法: {exc}", is_error=True)

    results: list[str] = []
    targets = [root] if root.is_file() else _iter_files(root, args.include)
    for path in targets:
        if _is_binary_file(path):
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line_no, line in enumerate(fh, start=1):
                    if regex.search(line.rstrip("\n")):
                        rel = _to_posix(path.relative_to(root))
                        results.append(f"{rel}:{line_no}: {line.rstrip()}")
                        if len(results) >= args.max_results:
                            content = (
                                f"匹配 {len(results)} 行（根目录 {root}，已达上限 "
                                f"{args.max_results}）:\n" + "\n".join(results)
                            )
                            return ToolResult(content=_truncate(content, ctx.max_output_chars))
        except OSError:
            continue
    content = f"匹配 {len(results)} 行（根目录 {root}）:\n" + "\n".join(results)
    return ToolResult(content=_truncate(content, ctx.max_output_chars))


def build_search_specs() -> list[tuple[ToolSpec, type[BaseModel], Any]]:
    """构建搜索工具的声明。

    Returns:
        (spec, args_model, handler) 三元组列表。
    """
    return [
        (
            ToolSpec(
                name="glob",
                description=(
                    "使用 glob 模式查找文件，如 '**/*.py'。用于定位项目结构、"
                    "查找特定类型文件，比 grep 适合按文件名搜索。"
                ),
                parameters=GlobArgs.model_json_schema(),
                read_only=True,
            ),
            GlobArgs,
            glob_files,
        ),
        (
            ToolSpec(
                name="grep",
                description=(
                    "按正则表达式搜索文件内容，返回 '文件:行号: 内容' 格式的匹配行。"
                    "用于查找代码中的函数定义、关键字、错误日志等。"
                ),
                parameters=GrepArgs.model_json_schema(),
                read_only=True,
            ),
            GrepArgs,
            grep_files,
        ),
    ]


def register_search(registry: ToolRegistry) -> None:
    """注册全部搜索工具。

    Args:
        registry: 目标注册表。
    """
    for spec, args_model, handler in build_search_specs():
        registry.register(spec, args_model, handler)
