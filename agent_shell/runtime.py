"""运行时配置：模型与凭据的热切换、掩码展示与持久化。

启动后（CLI REPL / Web 服务运行期间）用户可随时切换模型、更新各提供商的
API Key 与 Base URL，无需重启。修改实时生效，并持久化到用户级配置文件
（``~/.agent_shell/config.yaml`` 的 ``providers`` 段），下次启动自动加载。

凭据安全:
- 配置文件权限在 Windows 下无法限制，POSIX 下保存时尝试 chmod 600
- 对外展示（REST / CLI 列表）只返回掩码（如 ``sk-***abcd``），绝不回传明文
- 未配置的提供商回退到环境变量（兼容既有 ``OPENAI_API_KEY`` 等用法）
"""

from __future__ import annotations

import os
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agent_shell.config import DEFAULT_MODEL

USER_CONFIG_PATH = Path.home() / ".agent_shell" / "config.yaml"


@dataclass
class ProviderInfo:
    """单个提供商的运行时凭据。

    Attributes:
        name: 提供商名（与模型前缀一致，如 ``openai``）。
        api_key: 明文 API Key；None 表示未配置（回退环境变量）。
        api_base: 自定义 Base URL（兼容 OpenAI 协议的自建网关）；None 用默认。
        default_model: 该提供商常用的模型名（不含前缀，仅 UI 提示）。
    """

    name: str
    api_key: str | None = None
    api_base: str | None = None
    default_model: str | None = None

    def to_public(self) -> dict[str, Any]:
        """导出为可对外展示的字典（key 已掩码，不含明文）。

        Returns:
            掩码后的提供商信息。
        """
        return {
            "name": self.name,
            "api_key_masked": mask_key(self.api_key),
            "has_key": bool(self.api_key),
            "api_base": self.api_base,
            "default_model": self.default_model,
        }


def mask_key(key: str | None) -> str | None:
    """掩码 API Key：保留前 3 位与末 4 位，中间以 ``***`` 代替。

    Args:
        key: 明文 Key；None 返回 None。

    Returns:
        掩码串（如 ``sk-***abcd``）。
    """
    if not key:
        return None
    if len(key) <= 10:
        return "***" + key[-4:]
    return f"{key[:3]}***{key[-4:]}"


def _provider_prefix(model: str) -> str | None:
    """从 litellm 格式模型名中提取提供商前缀。

    Args:
        model: 模型名（如 ``openai/gpt-4o-mini``、``deepseek/deepseek-chat``）。

    Returns:
        提供商前缀；模型名不含 ``/`` 时返回 None。
    """
    head = model.split("/", 1)[0].strip()
    return head if head else None


class ProviderStore:
    """模型/凭据运行时存储（线程安全）。

    数据来源优先级（由高到低）:
    ``set_*`` 运行时修改 > 配置文件 ``providers`` 段 > 环境变量（回退，
    在 :meth:`resolve` 时兜底）。

    Args:
        path: 持久化文件路径；None 使用 ``~/.agent_shell/config.yaml``。
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or USER_CONFIG_PATH
        self._lock = threading.RLock()
        self._model = DEFAULT_MODEL
        self._providers: dict[str, ProviderInfo] = {}
        self._load()

    # ---------- 加载与保存 ----------

    def _load(self) -> None:
        """从磁盘加载 model 与 providers（文件不存在时保持默认）。"""
        if not self._path.is_file():
            return
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh)
        except (OSError, yaml.YAMLError):
            return
        if not isinstance(raw, dict):
            return
        model = raw.get("model")
        if isinstance(model, str) and model:
            self._model = model
        providers = raw.get("providers")
        if isinstance(providers, dict):
            for name, info in providers.items():
                if not isinstance(name, str) or not isinstance(info, dict):
                    continue
                api_key = info.get("api_key")
                api_base = info.get("api_base")
                default_model = info.get("default_model")
                self._providers[name] = ProviderInfo(
                    name=name,
                    api_key=api_key if isinstance(api_key, str) and api_key else None,
                    api_base=api_base if isinstance(api_base, str) and api_base else None,
                    default_model=(
                        default_model
                        if isinstance(default_model, str) and default_model
                        else None
                    ),
                )

    def save(self) -> None:
        """将当前 model 与 providers 写回配置文件（POSIX 下 chmod 600）。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {"model": self._model}
        if self._providers:
            data["providers"] = {
                name: {
                    "api_key": info.api_key,
                    "api_base": info.api_base,
                    "default_model": info.default_model,
                }
                for name, info in sorted(self._providers.items())
            }
        tmp = self._path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
        if os.name != "nt":
            with suppress(OSError):
                os.chmod(tmp, 0o600)
        tmp.replace(self._path)

    def seed_from_settings(self, settings: Any) -> None:
        """用启动配置初始化（仅填充未配置的项，不覆盖既有值）。

        模型与提供商仅在本存储尚无显式值（默认值/未配置）时填充，
        保证用户运行时的修改优先级高于启动配置。

        Args:
            settings: 已加载的 Settings（含 ``providers`` 与 ``model`` 属性）。
        """
        model = getattr(settings, "model", None)
        if isinstance(model, str) and model and self._model == DEFAULT_MODEL:
            with self._lock:
                self._model = model
        seed = getattr(settings, "providers", None)
        if not isinstance(seed, dict):
            return
        with self._lock:
            for name, info in seed.items():
                if name in self._providers:
                    continue
                if not isinstance(name, str) or not isinstance(info, dict):
                    continue
                api_key = info.get("api_key")
                api_base = info.get("api_base")
                self._providers[name] = ProviderInfo(
                    name=name,
                    api_key=api_key if isinstance(api_key, str) and api_key else None,
                    api_base=api_base if isinstance(api_base, str) else None,
                    default_model=(
                        info.get("default_model")
                        if isinstance(info.get("default_model"), str)
                        else None
                    ),
                )

    # ---------- 查询与修改 ----------

    @property
    def model(self) -> str:
        """当前激活的模型名。"""
        with self._lock:
            return self._model

    def set_model(self, model: str) -> None:
        """切换当前模型并持久化。

        Args:
            model: litellm 格式模型名（非空）。

        Raises:
            ValueError: 模型名为空。
        """
        model = model.strip()
        if not model:
            raise ValueError("模型名不能为空")
        with self._lock:
            self._model = model
        self.save()

    def get_provider(self, name: str) -> ProviderInfo | None:
        """按名称获取提供商信息。

        Args:
            name: 提供商名。

        Returns:
            提供商信息；未配置返回 None。
        """
        with self._lock:
            return self._providers.get(name)

    def upsert_provider(
        self,
        name: str,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        """新增或更新提供商凭据（None 表示不改动该字段）。

        Args:
            name: 提供商名。
            api_key: 新 API Key；None 保持原值。
            api_base: 新 Base URL；None 保持原值。
        """
        with self._lock:
            info = self._providers.get(name) or ProviderInfo(name=name)
            if api_key is not None:
                info.api_key = api_key.strip() or None
            if api_base is not None:
                base = api_base.strip()
                info.api_base = base or None
            self._providers[name] = info
        self.save()

    def list_providers(self) -> list[dict[str, Any]]:
        """列出全部提供商（key 已掩码）。

        Returns:
            可对外展示的提供商字典列表。
        """
        def sort_key(p: ProviderInfo) -> str:
            return p.name

        with self._lock:
            return [info.to_public() for info in sorted(self._providers.values(), key=sort_key)]

    def remove_provider(self, name: str) -> bool:
        """删除提供商配置。

        Args:
            name: 提供商名。

        Returns:
            是否成功删除。
        """
        with self._lock:
            if name in self._providers:
                del self._providers[name]
                self.save()
                return True
            return False

    def get_models_for_provider(self, provider: str) -> list[str]:
        """获取某提供商的推荐模型列表。

        Args:
            provider: 提供商名。

        Returns:
            模型名列表（带提供商前缀）。
        """
        # 常见提供商模型映射
        _MODELS_MAP: dict[str, list[str]] = {
            "openai": [
                "openai/gpt-4o",
                "openai/gpt-4o-mini",
                "openai/gpt-4-turbo",
                "openai/gpt-3.5-turbo",
                "openai/o1-preview",
                "openai/o1-mini",
            ],
            "anthropic": [
                "anthropic/claude-sonnet-4-20250514",
                "anthropic/claude-3-5-sonnet-20241022",
                "anthropic/claude-3-5-haiku-20241022",
                "anthropic/claude-3-opus-20240229",
            ],
            "dashscope": [
                "dashscope/qwen3-235b-a22b",
                "dashscope/qwen3-32b",
                "dashscope/qwen3-30b-a3b",
                "dashscope/qwen2.5-72b-instruct",
                "dashscope/qwen2.5-32b-instruct",
                "dashscope/qwen2.5-14b-instruct",
                "dashscope/qwen2.5-7b-instruct",
                "dashscope/qwen-turbo",
                "dashscope/qwen-plus",
                "dashscope/qwen-max",
            ],
            "google": [
                "gemini/gemini-2.0-flash",
                "gemini/gemini-1.5-pro",
                "gemini/gemini-1.5-flash",
            ],
            "ollama": [
                "ollama/llama3.1",
                "ollama/llama3.2",
                "ollama/qwen2.5",
                "ollama/codellama",
            ],
            "deepseek": [
                "deepseek/deepseek-chat",
                "deepseek/deepseek-coder",
            ],
            "groq": [
                "groq/llama-3.1-70b-versatile",
                "groq/llama-3.1-8b-instant",
                "groq/mixtral-8x7b-32768",
            ],
            "mistral": [
                "mistral/mistral-large-latest",
                "mistral/mistral-medium-latest",
                "mistral/mistral-small-latest",
                "mistral/open-mixtral-8x7b",
            ],
        }
        return _MODELS_MAP.get(provider.lower(), [])

    def resolve(self, model: str | None = None) -> tuple[str, str | None, str | None]:
        """解析模型的最终调用参数。

        Args:
            model: 模型名；None 使用当前激活模型。

        Returns:
            ``(model, api_key, api_base)`` 三元组。api_key/api_base 优先取
            配置文件中对应提供商的配置，否则回退环境变量（``<PREFIX>_API_KEY``
            与 ``<PREFIX>_API_BASE``，如 ``OPENAI_API_KEY``）。
        """
        with self._lock:
            target = (model or self._model).strip()
            prefix = _provider_prefix(target)
            info = self._providers.get(prefix) if prefix else None
            env_key = os.environ.get(f"{prefix.upper()}_API_KEY") if prefix else None
            env_base = os.environ.get(f"{prefix.upper()}_API_BASE") if prefix else None
            api_key = (info.api_key if info and info.api_key else None) or env_key
            api_base = (info.api_base if info and info.api_base else None) or env_base
            return target, api_key, api_base
