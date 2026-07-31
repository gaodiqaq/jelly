"""config 层：配置加载与校验测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_shell.config import DEFAULT_MODEL, _build_settings, load_settings
from agent_shell.errors import ConfigError


def test_load_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """无配置文件、无环境变量时使用默认值。"""
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    settings = load_settings()
    assert settings.model == DEFAULT_MODEL
    assert settings.permissions.default == "ask"
    assert settings.max_turns == 60
    assert settings.cwd == Path.cwd()


def test_load_settings_from_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """读取 YAML 配置文件。"""
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    config = tmp_path / "agent_shell.yaml"
    config.write_text(
        "model: deepseek/deepseek-chat\n"
        "max_turns: 10\n"
        "permissions:\n"
        "  default: auto\n"
        "tools:\n"
        "  bash_timeout: 30\n"
        "  disabled: [web_fetch]\n",
        encoding="utf-8",
    )
    settings = load_settings(config_path=config)
    assert settings.model == "deepseek/deepseek-chat"
    assert settings.max_turns == 10
    assert settings.permissions.default == "auto"
    assert settings.tools.bash_timeout == 30
    assert settings.tools.disabled == {"web_fetch"}


def test_load_settings_env_overrides_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量优先级高于配置文件。"""
    config = tmp_path / "agent_shell.yaml"
    config.write_text("model: openai/gpt-4o\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_MODEL", "openai/gpt-4o-mini")
    settings = load_settings(config_path=config)
    assert settings.model == "openai/gpt-4o-mini"


def test_load_settings_cli_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """命令行参数优先级高于环境变量。"""
    monkeypatch.setenv("AGENT_MODEL", "openai/gpt-4o")
    settings = load_settings(model="anthropic/claude-sonnet-4-5")
    assert settings.model == "anthropic/claude-sonnet-4-5"


def test_load_settings_missing_explicit_config(tmp_path: Path) -> None:
    """显式指定不存在的配置文件抛 ConfigError。"""
    with pytest.raises(ConfigError, match="配置文件不存在"):
        load_settings(config_path=tmp_path / "nope.yaml")


def test_load_settings_invalid_permission(tmp_path: Path) -> None:
    """非法的权限模式抛 ConfigError。"""
    config = tmp_path / "agent_shell.yaml"
    config.write_text("permissions:\n  default: maybe\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="ask/auto/deny"):
        load_settings(config_path=config)


def test_load_settings_invalid_yaml(tmp_path: Path) -> None:
    """YAML 语法错误抛 ConfigError。"""
    config = tmp_path / "agent_shell.yaml"
    config.write_text("model: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML"):
        load_settings(config_path=config)


def test_load_settings_non_mapping_root(tmp_path: Path) -> None:
    """根节点不是映射时抛 ConfigError。"""
    config = tmp_path / "agent_shell.yaml"
    config.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="映射"):
        load_settings(config_path=config)


def test_build_settings_invalid_max_turns() -> None:
    """max_turns 非整数抛 ConfigError。"""
    with pytest.raises(ConfigError, match="max_turns"):
        _build_settings({"max_turns": "abc"}, {}, None)


def test_settings_cwd_expansion() -> None:
    """cwd 自动扩展为用户绝对路径。"""
    settings = _build_settings({}, {"AGENT_CWD": "~"}, None)
    assert settings.cwd == Path.home().resolve()


def test_auto_permission_forces_read_only_auto_approve() -> None:
    """auto 模式下只读工具强制免审批。"""
    settings = _build_settings({}, {"AGENT_PERMISSION": "auto"}, None)
    assert settings.permissions.auto_approve_read_only is True
