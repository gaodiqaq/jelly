"""tools 层：文件系统工具测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_shell.tools import build_registry
from agent_shell.tools.base import ToolContext
from agent_shell.tools.fs import (
    EditArgs,
    ListDirArgs,
    ReadArgs,
    WriteArgs,
    edit_file,
    list_dir,
    read_file,
    write_file,
)


@pytest.fixture()
def ctx(tmp_path: Path) -> ToolContext:
    """临时工作目录的 ToolContext。"""
    return ToolContext(cwd=tmp_path)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_write_creates_parent_directories(ctx: ToolContext, tmp_path: Path) -> None:
    """write 自动创建缺失父目录。"""
    result = write_file(ctx, WriteArgs(path="a/b/c.txt", content="hello"))
    assert not result.is_error
    assert (tmp_path / "a" / "b" / "c.txt").read_text(encoding="utf-8") == "hello"


def test_write_refuses_overwrite_by_default(ctx: ToolContext, tmp_path: Path) -> None:
    """write 默认拒绝覆盖已存在文件。"""
    _write(tmp_path, "x.txt", "old")
    result = write_file(ctx, WriteArgs(path="x.txt", content="new"))
    assert result.is_error
    assert "已存在" in result.content
    assert (tmp_path / "x.txt").read_text(encoding="utf-8") == "old"


def test_write_overwrite_with_flag(ctx: ToolContext, tmp_path: Path) -> None:
    """设置 overwrite=true 时覆盖成功。"""
    _write(tmp_path, "x.txt", "old")
    result = write_file(ctx, WriteArgs(path="x.txt", content="new", overwrite=True))
    assert not result.is_error
    assert (tmp_path / "x.txt").read_text(encoding="utf-8") == "new"


def test_write_to_directory_path(ctx: ToolContext, tmp_path: Path) -> None:
    """写入目标是目录时报错。"""
    (tmp_path / "d").mkdir()
    result = write_file(ctx, WriteArgs(path="d", content="x"))
    assert result.is_error


def test_read_missing_file(ctx: ToolContext) -> None:
    """读取不存在的文件返回结构化错误。"""
    result = read_file(ctx, ReadArgs(path="nope.txt"))
    assert result.is_error
    assert "不存在" in result.content


def test_read_with_line_numbers_and_limits(ctx: ToolContext, tmp_path: Path) -> None:
    """read 返回带行号内容并支持 offset/limit。"""
    content = "\n".join(f"line {i}" for i in range(10))
    _write(tmp_path, "f.txt", content)
    result = read_file(ctx, ReadArgs(path="f.txt", offset=3, limit=2))
    assert not result.is_error
    lines = result.content.splitlines()
    assert any("3 | line 2" in line for line in lines)
    assert any("4 | line 3" in line for line in lines)
    assert "line 0" not in result.content


def test_read_binary_file(ctx: ToolContext, tmp_path: Path) -> None:
    """二进制文件拒绝以文本读取。"""
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01\x02")
    result = read_file(ctx, ReadArgs(path="bin.dat"))
    assert result.is_error
    assert "二进制" in result.content


def test_edit_success(ctx: ToolContext, tmp_path: Path) -> None:
    """edit 精确替换成功。"""
    _write(tmp_path, "f.py", "def foo():\n    return 1\n")
    result = edit_file(ctx, EditArgs(path="f.py", old_string="return 1", new_string="return 2"))
    assert not result.is_error
    assert "1 处" in result.content
    assert "return 2" in (tmp_path / "f.py").read_text(encoding="utf-8")


def test_edit_no_match(ctx: ToolContext, tmp_path: Path) -> None:
    """edit 无匹配时报错并保留文件。"""
    _write(tmp_path, "f.py", "hello")
    result = edit_file(ctx, EditArgs(path="f.py", old_string="world", new_string="x"))
    assert result.is_error
    assert "未找到" in result.content
    assert (tmp_path / "f.py").read_text(encoding="utf-8") == "hello"


def test_edit_multiple_matches_requires_replace_all(ctx: ToolContext, tmp_path: Path) -> None:
    """edit 多匹配且未指定 replace_all 时报错。"""
    _write(tmp_path, "f.py", "a\na\na")
    result = edit_file(ctx, EditArgs(path="f.py", old_string="a", new_string="b"))
    assert result.is_error
    assert "3 次" in result.content
    assert (tmp_path / "f.py").read_text(encoding="utf-8") == "a\na\na"


def test_edit_replace_all(ctx: ToolContext, tmp_path: Path) -> None:
    """edit 指定 replace_all 时替换全部。"""
    _write(tmp_path, "f.py", "a\na\na")
    result = edit_file(ctx, EditArgs(path="f.py", old_string="a", new_string="b", replace_all=True))
    assert not result.is_error
    assert "3 处" in result.content
    assert (tmp_path / "f.py").read_text(encoding="utf-8") == "b\nb\nb"


def test_edit_missing_file(ctx: ToolContext) -> None:
    """edit 不存在的文件报错。"""
    result = edit_file(ctx, EditArgs(path="nope.py", old_string="a", new_string="b"))
    assert result.is_error
    assert "不存在" in result.content


def test_list_dir_marks_directories(ctx: ToolContext, tmp_path: Path) -> None:
    """ls 区分目录与文件。"""
    (tmp_path / "sub").mkdir()
    _write(tmp_path, "a.txt", "x")
    result = list_dir(ctx, ListDirArgs(path="."))
    assert not result.is_error
    assert "[目录] sub/" in result.content
    assert "[文件] a.txt" in result.content


def test_list_dir_missing(ctx: ToolContext) -> None:
    """ls 不存在的目录报错。"""
    result = list_dir(ctx, ListDirArgs(path="nope"))
    assert result.is_error


def test_registry_dispatches_fs_tools(tmp_path: Path) -> None:
    """注册表完整链路：写文件 -> 读文件。"""
    registry = build_registry(cwd=tmp_path)
    written = registry.call("write", {"path": "r.txt", "content": "data"})
    assert not written.is_error
    read = registry.call("read", {"path": "r.txt"})
    assert not read.is_error
    assert "data" in read.content


def test_registry_unknown_tool(tmp_path: Path) -> None:
    """未知工具返回结构化错误。"""
    registry = build_registry(cwd=tmp_path)
    result = registry.call("nope", {})
    assert result.is_error
    assert "未知工具" in result.content


def test_registry_validation_error(tmp_path: Path) -> None:
    """参数校验失败返回结构化错误。"""
    registry = build_registry(cwd=tmp_path)
    result = registry.call("write", {"path": 123, "content": "x"})
    assert result.is_error
    assert "参数校验失败" in result.content
