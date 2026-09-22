"""Web CLI 的安全启动边界。"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from agent_shell import main as main_module
from agent_shell.config import Settings
from agent_shell.server import app as app_module


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(cwd=tmp_path, session_dir=tmp_path / "sessions")


def prepare_cli(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """隔离 dotenv 与本机用户配置。"""
    monkeypatch.setattr(main_module, "_load_dotenv_files", lambda: None)
    monkeypatch.setattr(main_module, "load_settings", lambda: settings)
    monkeypatch.delenv("AGENT_WEB_TOKEN", raising=False)
    monkeypatch.delenv("AGENT_WEB_USERS", raising=False)


def test_web_refuses_unauthenticated_non_loopback(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    prepare_cli(monkeypatch, settings)

    with pytest.raises(typer.Exit) as exc_info:
        main_module.serve_web(host="0.0.0.0", port=8000)

    assert exc_info.value.exit_code == 1


def test_web_allows_authenticated_non_loopback(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    prepare_cli(monkeypatch, settings)
    monkeypatch.setenv("AGENT_WEB_TOKEN", "test-only-token")
    application = object()
    called: dict[str, object] = {}
    monkeypatch.setattr(app_module, "create_app", lambda current: application)

    def fake_run(app: object, *, host: str, port: int) -> None:
        called.update(app=app, host=host, port=port)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    main_module.serve_web(host="0.0.0.0", port=4321)

    assert called == {"app": application, "host": "0.0.0.0", "port": 4321}
