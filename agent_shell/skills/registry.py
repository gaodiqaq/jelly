"""Skill 注册表：管理所有可用的 skills。

支持自动发现和手动注册。
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING

from agent_shell.skills.base import Skill, SkillResult

if TYPE_CHECKING:
    from agent_shell.core.session import Session


class SkillRegistry:
    """Skill 注册表。"""
    
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._trigger_map: dict[str, Skill] = {}
    
    def register(self, skill: Skill) -> None:
        """注册一个 skill 实例。"""
        self._skills[skill.name] = skill
        for trigger in skill.triggers:
            self._trigger_map[trigger.lower()] = skill
    
    def get(self, name: str) -> Skill | None:
        """通过名称获取 skill。"""
        return self._skills.get(name)
    
    def match(self, user_input: str) -> tuple[Skill, str, str] | None:
        """匹配用户输入到对应的 skill。
        
        Returns:
            (skill, trigger, args) 或 None
        """
        stripped = user_input.strip().lower()
        for trigger, skill in self._trigger_map.items():
            if stripped == trigger or stripped.startswith(trigger + " "):
                args = user_input.strip()[len(trigger):].strip()
                return skill, trigger, args
        return None
    
    def list_skills(self) -> list[dict]:
        """列出所有可用 skills。"""
        return [
            {
                "name": skill.name,
                "triggers": skill.triggers,
                "description": skill.description,
            }
            for skill in self._skills.values()
        ]
    
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
        result = skill.on_invoke(args, user_input, None)
        # 补充 skill 信息
        result.skill_name = skill.name
        result.description = skill.description
        # 计算剩余输入（去掉 trigger 部分）
        remaining = user_input.strip()
        if remaining.lower().startswith(trigger.lower()):
            remaining = remaining[len(trigger):].strip()
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
        # 补充 skill 信息
        result.skill_name = skill.name
        result.description = skill.description
        # 计算剩余输入（去掉 trigger 部分）
        remaining = user_input.strip()[len(trigger):].strip()
        result.remaining_input = remaining
        return result
    
    def auto_discover(self, package_name: str = "agent_shell.skills.builtins") -> int:
        """自动发现并注册指定包下的所有 skills。
        
        Returns:
            加载的 skill 数量
        """
        try:
            package = importlib.import_module(package_name)
        except ImportError:
            return 0
        
        count = 0
        for _, module_name, _ in pkgutil.iter_modules(package.__path__):
            try:
                module = importlib.import_module(f"{package_name}.{module_name}")
                # 查找模块中的 Skill 子类
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, Skill)
                        and attr is not Skill
                        and attr.name  # 确保已配置名称
                    ):
                        self.register(attr())
                        count += 1
            except Exception:
                pass  # 忽略加载失败的模块
        return count


# 全局单例
_global_registry: SkillRegistry | None = None


def get_global_registry() -> SkillRegistry:
    """获取全局 skill 注册表（懒加载）。"""
    global _global_registry
    if _global_registry is None:
        _global_registry = SkillRegistry()
        _global_registry.auto_discover()
    return _global_registry


def list_skills() -> list[dict]:
    """列出所有可用 skills。"""
    return get_global_registry().list_skills()
