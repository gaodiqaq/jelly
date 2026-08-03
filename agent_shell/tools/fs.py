"""文件系统工具：read / write / edit / ls。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent_shell.tools.base import ToolContext, ToolRegistry
from agent_shell.types import ToolResult, ToolSpec

MAX_OUTPUT_MARK = "\n...[输出已截断]..."


def _resolve(ctx: ToolContext, path: str) -> Path:
    """将用户路径解析为绝对路径。

    相对路径基于 ctx.cwd 解析；绝对路径保持原样。

    Args:
        ctx: 工具上下文。
        path: 用户提供的路径。

    Returns:
        规范化后的绝对路径。
    """
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = ctx.cwd / p
    return p.resolve()


def _is_binary(data: bytes) -> bool:
    """通过 NUL 字节检测是否为二进制内容。

    Args:
        data: 文件头部字节。

    Returns:
        是否为二进制文件。
    """
    return b"\x00" in data[:4096]


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


class ReadArgs(BaseModel):
    """read 工具参数。"""

    path: str = Field(description="要读取的文件路径（支持相对路径）")
    offset: int = Field(default=0, ge=0, description="起始行号（从 1 开始），默认 1")
    limit: int | None = Field(default=None, gt=0, description="最多读取的行数")


class WriteArgs(BaseModel):
    """write 工具参数。"""

    path: str = Field(description="要写入的文件路径")
    content: str = Field(description="文件完整内容")
    overwrite: bool = Field(default=False, description="文件已存在时是否覆盖（默认拒绝覆盖）")


class EditArgs(BaseModel):
    """edit 工具参数。"""

    path: str = Field(description="要修改的文件路径")
    old_string: str = Field(description="要替换的原始文本（必须精确匹配）")
    new_string: str = Field(description="替换后的新文本")
    replace_all: bool = Field(default=False, description="替换全部匹配（默认要求唯一匹配）")


class ListDirArgs(BaseModel):
    """ls 工具参数。"""

    path: str = Field(default=".", description="要列出的目录路径")
    limit: int = Field(default=200, ge=1, le=1000, description="最多显示的条目数")


def read_file(ctx: ToolContext, args: ReadArgs) -> ToolResult:
    """读取文本文件内容（带行号范围控制）。

    Args:
        ctx: 工具上下文。
        args: 路径与行号参数。

    Returns:
        ToolResult: 文件内容；文件不存在/是目录/二进制时 ``is_error=True``。
    """
    path = _resolve(ctx, args.path)
    if path.is_dir():
        return ToolResult(content=f"目标是一个目录: {path}", is_error=True)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return ToolResult(content=f"文件不存在: {path}", is_error=True)
    except PermissionError:
        return ToolResult(content=f"没有读取权限: {path}", is_error=True)
    if _is_binary(data):
        return ToolResult(
            content=f"文件疑似二进制（大小 {len(data)} 字节），无法以文本方式读取: {path}",
            is_error=True,
        )
    lines = data.decode("utf-8", errors="replace").splitlines()
    start = max(args.offset, 1) - 1
    selected = lines[start:] if args.limit is None else lines[start : start + args.limit]
    numbered = "\n".join(f"{start + i + 1:>6} | {line}" for i, line in enumerate(selected))
    header = f"{path}（{len(lines)} 行）\n"
    content = header + (numbered or "(空文件)")
    return ToolResult(content=_truncate(content, ctx.max_output_chars))


def write_file(ctx: ToolContext, args: WriteArgs) -> ToolResult:
    """创建或覆盖写入文件（自动创建父目录）。

    Args:
        ctx: 工具上下文。
        args: 路径、内容与覆盖策略。

    Returns:
        ToolResult: 写入成功返回文件路径；失败时 ``is_error=True``。
    """
    path = _resolve(ctx, args.path)
    if path.is_dir():
        return ToolResult(content=f"目标是一个目录: {path}", is_error=True)
    if path.exists() and not args.overwrite:
        return ToolResult(
            content=(
                f"文件已存在: {path}\n如需覆盖请设置 overwrite=true（或先用 read 确认内容后再覆盖）"
            ),
            is_error=True,
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args.content, encoding="utf-8")
    except PermissionError:
        return ToolResult(content=f"没有写入权限: {path}", is_error=True)
    except OSError as exc:
        return ToolResult(content=f"写入失败: {path}: {exc}", is_error=True)
    return ToolResult(content=f"已写入 {len(args.content)} 字符 -> {path}")


def edit_file(ctx: ToolContext, args: EditArgs) -> ToolResult:
    """在文件中执行精确字符串替换。

    Args:
        ctx: 工具上下文。
        args: 路径与替换参数。

    Returns:
        ToolResult: 替换成功返回替换次数；无匹配或多匹配（未指定 replace_all）时
        ``is_error=True``。
    """
    path = _resolve(ctx, args.path)
    if path.is_dir():
        return ToolResult(content=f"目标是一个目录: {path}", is_error=True)
    try:
        original = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ToolResult(content=f"文件不存在: {path}", is_error=True)
    except PermissionError:
        return ToolResult(content=f"没有读取权限: {path}", is_error=True)
    except UnicodeDecodeError:
        return ToolResult(content=f"文件不是 UTF-8 文本，无法编辑: {path}", is_error=True)

    if args.old_string not in original:
        return ToolResult(
            content=f"未找到匹配文本（old_string 必须精确匹配文件内容，注意空白字符）: {path}",
            is_error=True,
        )
    count = original.count(args.old_string)
    if count > 1 and not args.replace_all:
        return ToolResult(
            content=(
                f"old_string 在文件中出现 {count} 次，请提供更长的上下文使匹配唯一，"
                f"或设置 replace_all=true: {path}"
            ),
            is_error=True,
        )
    updated = original.replace(args.old_string, args.new_string)
    try:
        path.write_text(updated, encoding="utf-8")
    except PermissionError:
        return ToolResult(content=f"没有写入权限: {path}", is_error=True)
    except OSError as exc:
        return ToolResult(content=f"写入失败: {path}: {exc}", is_error=True)
    replaced = count if args.replace_all else 1
    return ToolResult(content=f"已在 {path} 中替换 {replaced} 处")


def list_dir(ctx: ToolContext, args: ListDirArgs) -> ToolResult:
    """列出目录内容（目录/文件分类，带大小与修改时间）。

    Args:
        ctx: 工具上下文。
        args: 目录与条目上限。

    Returns:
        ToolResult: 条目列表；目录不存在时 ``is_error=True``。
    """
    path = _resolve(ctx, args.path)
    if not path.is_dir():
        return ToolResult(content=f"目录不存在: {path}", is_error=True)
    entries: list[str] = []
    try:
        items = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return ToolResult(content=f"没有读取权限: {path}", is_error=True)
    for item in items[: args.limit]:
        if item.is_dir():
            entries.append(f"[目录] {item.name}/")
        else:
            try:
                size = item.stat().st_size
            except OSError:
                size = -1
            entries.append(f"[文件] {item.name} ({size} 字节)")
    total = len(items)
    if total > args.limit:
        entries.append(f"... 其余 {total - args.limit} 个条目未显示")
    content = f"{path}（共 {total} 个条目）\n" + "\n".join(entries)
    return ToolResult(content=_truncate(content, ctx.max_output_chars))


def build_fs_specs() -> list[tuple[ToolSpec, type[BaseModel], Any]]:
    """构建全部文件系统工具的声明。

    Returns:
        (spec, args_model, handler) 三元组列表。
    """
    return [
        (
            ToolSpec(
                name="read",
                description=(
                    "读取文本文件内容（带行号）。适用于查看源码、配置、日志等。"
                    "可用 offset/limit 参数控制读取范围。"
                ),
                parameters=ReadArgs.model_json_schema(),
                read_only=True,
            ),
            ReadArgs,
            read_file,
        ),
        (
            ToolSpec(
                name="write",
                description=(
                    "创建新文件或（设置 overwrite=true 时）覆盖已存在文件。"
                    "会创建缺失的父目录。写入前请先用 read 确认目标内容。"
                ),
                parameters=WriteArgs.model_json_schema(),
                read_only=False,
            ),
            WriteArgs,
            write_file,
        ),
        (
            ToolSpec(
                name="edit",
                description=(
                    "对已有文件执行精确字符串替换，用于修改现有文件而非整体重写。"
                    "old_string 必须与文件内容精确匹配且唯一（除非 replace_all=true）。"
                ),
                parameters=EditArgs.model_json_schema(),
                read_only=False,
            ),
            EditArgs,
            edit_file,
        ),
        (
            ToolSpec(
                name="ls",
                description="列出目录内容，区分文件与子目录并显示文件大小。",
                parameters=ListDirArgs.model_json_schema(),
                read_only=True,
            ),
            ListDirArgs,
            list_dir,
        ),
    ]


def register_fs(registry: ToolRegistry) -> None:
    """注册全部文件系统工具。

    Args:
        registry: 目标注册表。
    """
    for spec, args_model, handler in build_fs_specs():
        registry.register(spec, args_model, handler)
