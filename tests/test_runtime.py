"""runtime config 层：提供商凭据的持久化、掩码与解析测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_shell.config import DEFAULT_MODEL, Settings
from agent_shell.runtime import ProviderStore, mask_key


def test_mask_key() -> None:
    """掩码规则：保留前 3 位与末 4 位。"""
    assert mask_key(None) is None
    assert mask_key("") is None
    assert mask_key("abcdefg") == "***defg"
    assert mask_key("sk-123456789abcd") == "sk-***abcd"


def test_default_store(tmp_path: Path) -> None:
    """初始状态：默认模型，无提供商。"""
    store = ProviderStore(tmp_path / "runtime.yaml")
    assert store.model == DEFAULT_MODEL
    assert store.list_providers() == []


def test_persist_roundtrip(tmp_path: Path) -> None:
    """模型与提供商变更后重新加载保持一致（写入的 Key 为明文未损坏）。"""
    path = tmp_path / "runtime.yaml"
    store = ProviderStore(path)
    store.set_model("deepseek/deepseek-chat")
    store.upsert_provider(
        "deepseek", api_key="sk-ds-secret-1234", api_base="https://api.deepseek.com"
    )
    store.upsert_provider("openai", api_key="sk-openai-secret")

    reloaded = ProviderStore(path)
    assert reloaded.model == "deepseek/deepseek-chat"
    info = reloaded.get_provider("deepseek")
    assert info is not None
    assert info.api_key == "sk-ds-secret-1234"
    assert info.api_base == "https://api.deepseek.com"
    assert reloaded.get_provider("openai").api_key == "sk-openai-secret"


def test_list_providers_masked(tmp_path: Path) -> None:
    """列表只返回掩码 Key，不暴露明文。"""
    store = ProviderStore(tmp_path / "runtime.yaml")
    store.upsert_provider("openai", api_key="sk-secret1234")
    public = store.list_providers()[0]
    assert public["name"] == "openai"
    assert public["has_key"] is True
    assert public["api_key_masked"] == "sk-***1234"
    rendered = str(public)
    assert "sk-secret1234" not in rendered


def test_resolve_prefers_store_over_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve：store 中 Key 优先于环境变量；未配置时回退环境变量。"""
    store = ProviderStore(tmp_path / "runtime.yaml")
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setenv("OPENAI_API_BASE", "https://env.example.com")

    _, key, base = store.resolve("openai/gpt-4o-mini")
    assert key == "env-key"
    assert base == "https://env.example.com"

    store.upsert_provider("openai", api_key="store-key", api_base="https://store.example.com")
    _, key, base = store.resolve("openai/gpt-4o-mini")
    assert key == "store-key"
    assert base == "https://store.example.com"


def test_resolve_without_provider_uses_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """无路径前缀的自定义模型名也能从环境变量回退。"""
    store = ProviderStore(tmp_path / "runtime.yaml")
    monkeypatch.setenv("MYGATE_API_KEY", "gate-key")
    model, key, base = store.resolve("mygate/v1")
    assert model == "mygate/v1"
    assert key == "gate-key"
    assert base is None


def test_seed_from_settings_seeds_model_and_providers(tmp_path: Path) -> None:
    """seed 用启动配置兜底模型与提供商（store 为空时）。"""
    settings = Settings(
        model="openai/gpt-4o-mini",
        session_dir=tmp_path / "sessions",
        providers={"openai": {"api_key": "sk-seed-1234", "api_base": None, "default_model": None}},
    )
    store = ProviderStore(tmp_path / "runtime.yaml")
    store.seed_from_settings(settings)
    assert store.model == "openai/gpt-4o-mini"
    assert store.get_provider("openai").api_key == "sk-seed-1234"


def test_seed_does_not_override_existing(tmp_path: Path) -> None:
    """seed 不覆盖 store 已存在的模型与提供商。"""
    store = ProviderStore(tmp_path / "runtime.yaml")
    store.set_model("openai/gpt-4o")
    store.upsert_provider("openai", api_key="sk-existing")
    settings = Settings(
        model="openai/gpt-4o-mini",
        session_dir=tmp_path / "sessions",
        providers={"openai": {"api_key": "sk-seed"}},
    )
    store.seed_from_settings(settings)
    assert store.model == "openai/gpt-4o"
    assert store.get_provider("openai").api_key == "sk-existing"


def test_set_model_rejects_empty(tmp_path: Path) -> None:
    """空模型名抛 ValueError。"""
    store = ProviderStore(tmp_path / "runtime.yaml")
    with pytest.raises(ValueError):
        store.set_model("   ")