"""server 层：工作台文件 API（/api/files、/api/file、/api/file/raw）测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_shell.config import PermissionsConfig, Settings
from agent_shell.runtime import ProviderStore
from agent_shell.server.app import create_app
from agent_shell.server.manager import SessionManager
from agent_shell.types import AssistantMessage


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """构造一个工作区目录树：两个目录 + 三类文件（文本/空/二进制）。"""
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "README.md").write_text("# 标题\n\n正文内容\n", encoding="utf-8")
    (root / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "docs" / "empty.txt").write_text("", encoding="utf-8")
    (root / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    return root


@pytest.fixture()
def client(workspace: Path, tmp_path: Path) -> TestClient:
    """注入工作区配置的测试客户端（禁用鉴权）。"""
    settings = Settings(
        model="scripted-model",
        session_dir=tmp_path / "sessions",
        cwd=workspace,
        permissions=PermissionsConfig(default="auto"),
    )
    store = ProviderStore(tmp_path / "runtime.yaml")
    manager = SessionManager(settings, ScriptedLLM(), store=store)
    return TestClient(create_app(settings, manager, store=store))


class ScriptedLLM:
    """无调用的 LLM 替身。"""

    model = "scripted"

    def complete(self, messages, tools=None, *, stream=True, on_token=None, **_):  # pragma: no cover
        raise AssertionError("文件 API 测试不应调用 LLM")


def test_list_root_dirs_first(client: TestClient) -> None:
    """根目录列表：目录排前、包含类型与大小。"""
    data = client.get("/api/files").json()
    assert data["path"] == "."
    entries = data["entries"]
    names = [e["name"] for e in entries]
    assert set(names) == {"src", "docs", "README.md", "blob.bin"}
    assert names.index("src") < names.index("README.md")
    readme = next(e for e in entries if e["name"] == "README.md")
    assert readme["type"] == "file"
    assert readme["size"] > 0
    src = next(e for e in entries if e["name"] == "src")
    assert src["type"] == "dir"


def test_list_subdirectory(client: TestClient) -> None:
    """子目录列表与相对路径回显。"""
    data = client.get("/api/files", params={"path": "src"}).json()
    assert data["path"] == "src"
    assert [e["name"] for e in data["entries"]] == ["main.py"]


def test_list_missing_dir_404(client: TestClient) -> None:
    """不存在的目录返回 404。"""
    assert client.get("/api/files", params={"path": "nope"}).status_code == 404


def test_read_text_file(client: TestClient) -> None:
    """读取文本文件：内容、相对路径、大小。"""
    data = client.get("/api/file", params={"path": "src/main.py"}).json()
    assert data["path"] == "src/main.py"
    assert data["name"] == "main.py"
    assert data["is_binary"] is False
    assert "print" in data["content"]


def test_read_file_accepts_absolute_path_inside_root(client: TestClient, workspace: Path) -> None:
    """工作区内的绝对路径可读（agent 回复里的完整路径可点击）。"""
    data = client.get("/api/file", params={"path": str(workspace / "README.md")}).json()
    assert data["name"] == "README.md"
    assert "标题" in data["content"]


def test_read_binary_file_flagged(client: TestClient) -> None:
    """二进制文件：is_binary=True，content 为 None。"""
    data = client.get("/api/file", params={"path": "blob.bin"}).json()
    assert data["is_binary"] is True
    assert data["content"] is None


def test_read_large_file_truncated(client: TestClient, workspace: Path) -> None:
    """超过预览上限的文件被截断并带标记。"""
    big = workspace / "big.log"
    big.write_text("x" * 300_000, encoding="utf-8")
    data = client.get("/api/file", params={"path": "big.log"}).json()
    assert data["truncated"] is True
    assert len(data["content"]) < 300_000


def test_read_missing_file_404(client: TestClient) -> None:
    """不存在的文件返回 404。"""
    assert client.get("/api/file", params={"path": "nope.py"}).status_code == 404


def test_path_traversal_rejected(client: TestClient, tmp_path: Path) -> None:
    """越出工作区的路径（../、工作区外绝对路径）一律 400。"""
    assert client.get("/api/file", params={"path": "../outside.txt"}).status_code == 400
    assert client.get("/api/files", params={"path": ".."}).status_code == 400
    assert client.get("/api/file", params={"path": str(tmp_path / "outside.txt")}).status_code == 400


def test_raw_file_served(client: TestClient, workspace: Path) -> None:
    """raw 端点返回原始字节与猜测的媒体类型。"""
    svg = workspace / "pic.svg"
    svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8")
    resp = client.get("/api/file/raw", params={"path": "pic.svg"})
    assert resp.status_code == 200
    assert "svg" in resp.headers["content-type"]
    assert b"<svg" in resp.content


def test_raw_missing_file_404(client: TestClient) -> None:
    """raw 端点对不存在文件返回 404。"""
    assert client.get("/api/file/raw", params={"path": "nope.png"}).status_code == 404
