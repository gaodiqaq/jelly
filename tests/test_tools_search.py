"""tools 层：搜索工具测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_shell.tools.base import ToolContext
from agent_shell.tools.search import GlobArgs, GrepArgs, glob_files, grep_files


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """构造一个小型测试项目。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        "import os\n\ndef main():\n    return os.getcwd()\n", encoding="utf-8"
    )
    (tmp_path / "src" / "util.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("module.exports = 1;\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def ctx(project: Path) -> ToolContext:
    """以测试项目为 cwd 的 ToolContext。"""
    return ToolContext(cwd=project)


def test_glob_finds_python_files(ctx: ToolContext) -> None:
    """glob 递归查找 .py 文件。"""
    result = glob_files(ctx, GlobArgs(pattern="**/*.py", path="."))
    assert not result.is_error
    assert "src/main.py" in result.content
    assert "src/util.py" in result.content
    assert "README.md" not in result.content


def test_glob_single_level(ctx: ToolContext) -> None:
    """glob 非递归模式只匹配当前层。"""
    result = glob_files(ctx, GlobArgs(pattern="*.md", path="."))
    assert not result.is_error
    assert "README.md" in result.content
    assert "src/main.py" not in result.content


def test_glob_missing_root(ctx: ToolContext) -> None:
    """glob 根目录不存在报错。"""
    result = glob_files(ctx, GlobArgs(pattern="**/*", path="nope"))
    assert result.is_error


def test_grep_finds_matches(ctx: ToolContext) -> None:
    """grep 返回 文件:行号: 内容 格式。"""
    result = grep_files(ctx, GrepArgs(pattern="def main", path="."))
    assert not result.is_error
    assert "src/main.py:3" in result.content


def test_grep_include_filter(ctx: ToolContext) -> None:
    """include 过滤文件类型。"""
    result = grep_files(ctx, GrepArgs(pattern="Demo", path=".", include="*.md"))
    assert not result.is_error
    assert "README.md:1" in result.content


def test_grep_case_insensitive(ctx: ToolContext) -> None:
    """case_insensitive 生效。"""
    result = grep_files(
        ctx,
        GrepArgs(pattern="demo", path=".", include="*.md", case_insensitive=True),
    )
    assert not result.is_error
    assert "README.md" in result.content


def test_grep_skips_node_modules(ctx: ToolContext) -> None:
    """grep 跳过 node_modules。"""
    result = grep_files(ctx, GrepArgs(pattern="module.exports", path="."))
    assert not result.is_error
    matched = [line for line in result.content.splitlines() if ":" in line]
    assert not any(line.startswith("node_modules") for line in matched)


def test_grep_invalid_regex(ctx: ToolContext) -> None:
    """非法正则返回结构化错误。"""
    result = grep_files(ctx, GrepArgs(pattern="[unclosed", path="."))
    assert result.is_error
    assert "正则" in result.content


def test_grep_no_match(ctx: ToolContext) -> None:
    """无匹配时返回 0 行提示（非错误）。"""
    result = grep_files(ctx, GrepArgs(pattern="zzz_not_exists", path="."))
    assert not result.is_error
    assert "0 行" in result.content
