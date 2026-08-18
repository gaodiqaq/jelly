"""配置加载与校验。

配置来源优先级（从高到低）:
1. 命令行参数（由 main.py 注入）
2. 环境变量（``AGENT_MODEL`` / ``AGENT_PERMISSION`` / ``AGENT_CWD`` / ``AGENT_MAX_TURNS``）
3. 配置文件（``--config`` 指定，或默认搜索 ``./agent_shell.yaml``、
   ``~/.agent_shell/config.yaml``）
4. 内置默认值

配置校验失败抛出 :class:`agent_shell.errors.ConfigError`。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from agent_shell.errors import ConfigError

DEFAULT_MODEL = "openai/gpt-4o-mini"
CONFIG_FILE_NAMES = ("agent_shell.yaml", "config.yaml")


class PermissionsConfig:
    """权限相关配置。

    Attributes:
        default: 默认权限模式，``ask``（逐个询问）/ ``auto``（全自动）/
            ``deny``（全部拒绝）。
        auto_approve_read_only: 只读工具是否免审批。
    """

    def __init__(self, default: str = "ask", auto_approve_read_only: bool = True) -> None:
        self.default = default
        self.auto_approve_read_only = auto_approve_read_only

    @property
    def is_auto(self) -> bool:
        """是否全自动审批。"""
        return self.default == "auto"

    @property
    def is_deny(self) -> bool:
        """是否默认拒绝所有工具调用。"""
        return self.default == "deny"


class ToolsConfig:
    """工具执行相关配置。

    Attributes:
        bash_timeout: bash 工具默认超时秒数。
        max_output_chars: 工具输出截断上限（字符）。
        disabled: 被禁用的工具名集合（空集合表示全部启用）。
    """

    def __init__(
        self,
        bash_timeout: float = 120.0,
        max_output_chars: int = 30000,
        disabled: set[str] | None = None,
    ) -> None:
        self.bash_timeout = bash_timeout
        self.max_output_chars = max_output_chars
        self.disabled = disabled or set()

    def is_enabled(self, tool_name: str) -> bool:
        """判断工具是否启用。

        Args:
            tool_name: 工具名称。

        Returns:
            工具是否在禁用名单之外。
        """
        return tool_name not in self.disabled


class ContextConfig:
    """上下文管理配置。

    Attributes:
        max_chars: 上下文窗口估算上限（字符），超出后从最旧消息裁剪。
    """

    def __init__(self, max_chars: int = 120000) -> None:
        self.max_chars = max_chars


class APIConfig:
    """模型调用参数配置。

    Attributes:
        temperature: 采样温度。
        max_tokens: 最大生成 token 数（None 使用提供商默认值）。
        timeout: 单次请求超时秒数。
    """

    def __init__(
        self,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout


class Settings:
    """全局配置聚合对象。

    Attributes:
        model: litellm 格式模型名（含提供商前缀，如 ``openai/gpt-4o-mini``）。
        max_turns: 单轮用户输入内最大工具调用循环次数。
        system_prompt: 自定义系统提示词，None 使用内置默认。
        api: 模型调用参数。
        permissions: 权限配置。
        tools: 工具执行配置。
        context: 上下文裁剪配置。
        session_dir: 会话文件存储目录。
        cwd: Agent 的默认工作目录（工具执行基目录）。
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        max_turns: int = 60,
        system_prompt: str | None = None,
        api: APIConfig | None = None,
        permissions: PermissionsConfig | None = None,
        tools: ToolsConfig | None = None,
        context: ContextConfig | None = None,
        session_dir: Path | None = None,
        cwd: Path | None = None,
        providers: dict[str, dict] | None = None,
    ) -> None:
        self.model = model
        self.max_turns = max_turns
        self.system_prompt = system_prompt
        self.api = api or APIConfig()
        self.permissions = permissions or PermissionsConfig()
        self.tools = tools or ToolsConfig()
        self.context = context or ContextConfig()
        self.session_dir = session_dir or Path.home() / ".agent_shell" / "sessions"
        self.cwd = cwd or Path.cwd()
        self.providers: dict[str, dict] = dict(providers or {})

    @property
    def session_dir(self) -> Path:
        """会话存储目录（绝对路径）。"""
        return self._session_dir

    @session_dir.setter
    def session_dir(self, value: Path) -> None:
        self._session_dir = value.expanduser().resolve()

    @property
    def cwd(self) -> Path:
        """Agent 工作目录（绝对路径）。"""
        return self._cwd

    @cwd.setter
    def cwd(self, value: Path) -> None:
        self._cwd = value.expanduser().resolve()


def _locate_config_file(explicit: Path | None) -> Path | None:
    """定位配置文件。

    Args:
        explicit: 用户显式指定的配置文件路径。

    Returns:
        配置文件路径；未找到返回 None。

    Raises:
        ConfigError: 显式指定的配置文件不存在。
    """
    if explicit is not None:
        path = explicit.expanduser()
        if not path.is_file():
            raise ConfigError(f"配置文件不存在: {path}")
        return path
    candidates: list[Path] = [Path.cwd() / name for name in CONFIG_FILE_NAMES]
    candidates.append(Path.home() / ".agent_shell" / CONFIG_FILE_NAMES[1])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _read_config_file(path: Path) -> dict:
    """读取并解析 YAML 配置文件。

    Args:
        path: 配置文件路径。

    Returns:
        解析后的字典。

    Raises:
        ConfigError: 文件无法读取或 YAML 语法错误。
    """
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件 YAML 语法错误: {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"无法读取配置文件 {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(
            f"配置文件根节点必须是映射（YAML 对象），实际为 {type(data).__name__}: {path}"
        )
    return data


def _build_settings(raw: dict, env: dict[str, str], explicit_cwd: Path | None) -> Settings:
    """从配置字典与环境变量构建 Settings 对象。

    Args:
        raw: 配置文件解析出的字典。
        env: 环境变量映射（默认 os.environ）。
        explicit_cwd: 命令行指定的工作目录。

    Returns:
        构建完成的 Settings。

    Raises:
        ConfigError: 字段类型非法或权限模式取值非法。
    """
    model = env.get("AGENT_MODEL", raw.get("model", DEFAULT_MODEL))
    if not isinstance(model, str) or not model:
        raise ConfigError(f"model 必须是非空字符串，实际为 {model!r}")

    max_turns = raw.get("max_turns", 60)
    if env.get("AGENT_MAX_TURNS"):
        max_turns = env["AGENT_MAX_TURNS"]
    try:
        max_turns = int(max_turns)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"max_turns 必须是整数，实际为 {max_turns!r}") from exc
    if max_turns < 1:
        raise ConfigError(f"max_turns 必须大于 0，实际为 {max_turns}")

    system_prompt = raw.get("system_prompt")

    api_raw = raw.get("api") or {}
    if not isinstance(api_raw, dict):
        raise ConfigError(f"api 必须是映射，实际为 {type(api_raw).__name__}")
    api = APIConfig(
        temperature=api_raw.get("temperature", 0.2),
        max_tokens=api_raw.get("max_tokens"),
        timeout=api_raw.get("timeout", 120.0),
    )

    permissions_raw = raw.get("permissions") or {}
    if not isinstance(permissions_raw, dict):
        raise ConfigError(f"permissions 必须是映射，实际为 {type(permissions_raw).__name__}")
    permission = env.get("AGENT_PERMISSION", permissions_raw.get("default", "ask"))
    if permission not in ("ask", "auto", "deny"):
        raise ConfigError(f"permissions.default 必须是 ask/auto/deny 之一，实际为 {permission!r}")
    permissions = PermissionsConfig(
        default=permission,
        auto_approve_read_only=permissions_raw.get("auto_approve_read_only", True),
    )

    tools_raw = raw.get("tools") or {}
    if not isinstance(tools_raw, dict):
        raise ConfigError(f"tools 必须是映射，实际为 {type(tools_raw).__name__}")
    disabled_raw = tools_raw.get("disabled") or []
    if not isinstance(disabled_raw, list) or not all(isinstance(n, str) for n in disabled_raw):
        raise ConfigError(f"tools.disabled 必须是字符串列表，实际为 {disabled_raw!r}")
    tools = ToolsConfig(
        bash_timeout=tools_raw.get("bash_timeout", 120.0),
        max_output_chars=tools_raw.get("max_output_chars", 30000),
        disabled=set(disabled_raw),
    )

    context_raw = raw.get("context") or {}
    if not isinstance(context_raw, dict):
        raise ConfigError(f"context 必须是映射，实际为 {type(context_raw).__name__}")
    context = ContextConfig(max_chars=context_raw.get("max_chars", 120000))

    session_dir = Path(env.get("AGENT_SESSION_DIR", str(Path.home() / ".agent_shell" / "sessions")))

    cwd: Path | None = explicit_cwd
    if cwd is None and env.get("AGENT_CWD"):
        cwd = Path(env["AGENT_CWD"])

    providers_raw = raw.get("providers") or {}
    if not isinstance(providers_raw, dict):
        raise ConfigError(f"providers 必须是映射，实际为 {type(providers_raw).__name__}")
    providers: dict[str, dict] = {}
    for name, info in providers_raw.items():
        if not isinstance(name, str) or not isinstance(info, dict):
            raise ConfigError(f"providers.{name} 必须是映射")
        providers[name] = {
            "api_key": info.get("api_key"),
            "api_base": info.get("api_base"),
            "default_model": info.get("default_model"),
        }

    settings = Settings(
        model=model,
        max_turns=max_turns,
        system_prompt=system_prompt,
        api=api,
        permissions=permissions,
        tools=tools,
        context=context,
        session_dir=session_dir,
        cwd=cwd,
        providers=providers,
    )
    if settings.permissions.default == "auto":
        settings.permissions.auto_approve_read_only = True
    return settings


def load_settings(
    config_path: Path | None = None,
    *,
    model: str | None = None,
    max_turns: int | None = None,
    permission: str | None = None,
    cwd: Path | None = None,
) -> Settings:
    """按优先级加载配置（命令行 > 环境变量 > 配置文件 > 默认值）。

    Args:
        config_path: 显式配置文件路径。
        model: 命令行覆盖的模型名。
        max_turns: 命令行覆盖的最大轮数。
        permission: 命令行覆盖的权限模式。
        cwd: 命令行覆盖的工作目录。

    Returns:
        完整配置对象。

    Raises:
        ConfigError: 任一来源的配置非法。
    """
    raw: dict = {}
    located = _locate_config_file(config_path)
    if located is not None:
        raw = _read_config_file(located)

    overrides: dict[str, str] = {}
    if model is not None:
        overrides["AGENT_MODEL"] = model
    if max_turns is not None:
        overrides["AGENT_MAX_TURNS"] = str(max_turns)
    if permission is not None:
        overrides["AGENT_PERMISSION"] = permission
    if cwd is not None:
        overrides["AGENT_CWD"] = str(cwd)

    merged_env = {**os.environ, **overrides}
    return _build_settings(raw, merged_env, cwd)
