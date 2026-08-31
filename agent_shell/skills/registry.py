"""Skill 注册表：管理所有可用的 skills。

扫描两个来源:
1. 内置: ``agent_shell/skills/builtins/`` 下的 Python Skill 子类
2. 用户安装: ``~/.agent_shell/skills/*/SKILL.md`` 声明式 skill
   （MarkdownSkill，含 references/scripts/assets 资源）

触发机制:
- 斜杠命令（``/review``、``/chinese-novelist``）
- 自然语言触发词（SKILL.md front matter 的 ``metadata.trigger``，逗号分隔）
- 模型自主触发：system prompt 注入所有 skill 的 name+description+triggers，
  模型判断用户意图后主动激活

支持热重载（reload）：清空后重新扫描，新增/卸载即时生效。
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path
from typing import TYPE_CHECKING

from agent_shell.skills.base import MarkdownSkill, Skill, SkillResult
from agent_shell.skills.installer import (
    list_installed,
    parse_skill_markdown,
)

if TYPE_CHECKING:
    from agent_shell.core.session import Session

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Skill 注册表。"""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._trigger_map: dict[str, Skill] = {}

    # ---------- 注册与发现 ----------

    def register(self, skill: Skill) -> None:
        """注册一个 skill 实例（重名时覆盖并告警）。"""
        if skill.name in self._skills:
            logger.warning("skill 重名，后者覆盖前者: %s", skill.name)
        self._skills[skill.name] = skill
        for trigger in skill.triggers:
            self._trigger_map[trigger.lower()] = skill

    def auto_discover(self, package_name: str = "agent_shell.skills.builtins") -> int:
        """自动发现并注册内置 Python skills + 用户安装的 SKILL.md skills。

        Returns:
            加载的 skill 数量。
        """
        count = 0
        # 1. 内置 Python skill
        try:
            package = importlib.import_module(package_name)
        except ImportError:
            package = None
        if package is not None:
            for _, module_name, _ in pkgutil.iter_modules(package.__path__):
                try:
                    module = importlib.import_module(f"{package_name}.{module_name}")
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if (
                            isinstance(attr, type)
                            and issubclass(attr, Skill)
                            and attr is not Skill
                            and attr is not MarkdownSkill
                            and attr.name  # 确保已配置名称
                        ):
                            self.register(attr())
                            count += 1
                except Exception as exc:
                    logger.warning("跳过加载失败的内置 skill 模块 %s: %s", module_name, exc)
        # 2. 用户安装的 SKILL.md
        for item in list_installed():
            try:
                skill_md = Path(item["path"]) / "SKILL.md"
                definition = parse_skill_markdown(skill_md.read_text(encoding="utf-8"))
                self.register(definition.to_skill(resource_dir=str(skill_md.parent)))
                count += 1
            except Exception as exc:
                logger.warning("跳过加载失败的用户 skill %s: %s", item["path"], exc)
        return count

    def reload(self) -> int:
        """热重载：清空全部 skill 后重新扫描。"""
        self._skills.clear()
        self._trigger_map.clear()
        return self.auto_discover()

    # ---------- 查询 ----------

    def get(self, name: str) -> Skill | None:
        """通过名称获取 skill。"""
        return self._skills.get(name)

    def match(self, user_input: str) -> tuple[Skill, str, str] | None:
        """匹配用户输入到对应的 skill（斜杠命令 + 自然语言触发词）。

        匹配优先级:
        1. 精确触发词（``/chinese-novelist``、front matter 声明的自然语言短语，
           触发词后跟空格/标点/结束均算匹配）
        2. 用户输入引用 skill 名（"使用 chinese-novelist 写小说"、
           "用 chinese-novelist 帮我…"）

        Returns:
            (skill, trigger, args) 或 None
        """
        stripped = user_input.strip()
        lowered = stripped.lower()
        # 1. 精确触发词
        for trigger, skill in self._trigger_map.items():
            t = trigger.lower()
            if lowered == t or lowered.startswith(t + " "):
                args = stripped[len(trigger) :].strip()
                return skill, trigger, args
            # 触发词后直接跟中文标点（"创作中文小说，题材…"）也算匹配
            punctuation = "，,、。.!！?？;；:："
            if lowered.startswith(t) and len(lowered) > len(t) and lowered[len(t)] in punctuation:
                args = stripped[len(trigger) :].lstrip(punctuation + " ")
                return skill, trigger, args
        # 2. 引用 skill 名（"使用/用/启用/激活 <name>" 形式，允许中间空格）
        for name, skill in self._skills.items():
            lowered_name = name.lower()
            # 短名（<=3字符）只接受斜杠命令，避免误匹配普通句子
            if len(name) <= 3:
                continue
            # 动词前缀（中英文），允许后跟任意空白再跟 skill 名
            verbs = ("使用", "用", "启用", "激活", "use", "using")
            for verb in verbs:
                vi = lowered.find(verb.lower())
                if vi == -1:
                    continue
                after_verb = vi + len(verb)
                # 动词后必须是空格/冒号等分隔符，避免 "用户" 之类的误匹配
                if after_verb < len(lowered) and lowered[after_verb] not in " \t:：":
                    continue
                rest = lowered[after_verb:].lstrip(" \t:：")
                if rest.startswith(lowered_name):
                    name_len = len(lowered_name)
                    tail = stripped[vi + len(verb) :].lstrip(" \t:：")
                    args = tail[name_len:].strip(" ，,、。:：")
                    return skill, name, args
        return None

    def list_skills(self) -> list[dict]:
        """列出所有可用 skills。"""
        return [
            {
                "name": skill.name,
                "triggers": skill.triggers,
                "description": skill.description,
                "type": ("markdown" if isinstance(skill, MarkdownSkill) else "python"),
                "resource_dir": getattr(skill, "_resource_dir", "") or "",
            }
            for skill in self._skills.values()
        ]

    # ---------- 调用 ----------

    def invoke(
        self,
        user_input: str,
        session: Session | None = None,
    ) -> SkillResult | None:
        """尝试调用匹配的 skill。

        Args:
            user_input: 用户输入
            session: 当前会话

        Returns:
            SkillResult 或 None（无匹配）
        """
        match = self.match(user_input)
        if match is None:
            return None
        skill, trigger, args = match
        result = skill.on_invoke(args, user_input, session)
        # 补充 skill 信息
        result.skill_name = skill.name
        result.description = skill.description
        remaining = user_input.strip()
        if remaining.lower().startswith(trigger.lower()):
            remaining = remaining[len(trigger) :].strip()
        result.remaining_input = remaining
        return result

    def detect_and_invoke(self, user_input: str) -> SkillResult:
        """检测并调用匹配的 skill（供 manager 使用）。

        Args:
            user_input: 用户输入

        Returns:
            SkillResult（始终返回，无匹配时 handled=False）
        """
        match = self.match(user_input)
        if match is None:
            return SkillResult(handled=False)
        skill, trigger, args = match
        result = skill.on_invoke(args, user_input, None)
        result.skill_name = skill.name
        result.description = skill.description
        remaining = user_input.strip()
        if remaining.lower().startswith(trigger.lower()):
            remaining = remaining[len(trigger) :].strip()
        result.remaining_input = remaining
        return result


# 全局单例
_global_registry: SkillRegistry | None = None


def get_global_registry() -> SkillRegistry:
    """获取全局 skill 注册表（懒加载）。"""
    global _global_registry
    if _global_registry is None:
        _global_registry = SkillRegistry()
        _global_registry.auto_discover()
    return _global_registry


def reload_global_registry() -> SkillRegistry:
    """热重载全局注册表（安装/卸载后调用）。"""
    global _global_registry
    registry = get_global_registry()
    registry.reload()
    return registry


def list_skills() -> list[dict]:
    """列出所有可用 skills。"""
    return get_global_registry().list_skills()
