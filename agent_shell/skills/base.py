"""Skill 基类与数据结构。

Skill 是一个可复用的"能力包"，包含：
- 名称与触发词
- 增强的系统提示词
- 自定义处理逻辑
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_shell.core.session import Session


@dataclass
class SkillResult:
    """Skill 处理结果。"""
    # 是否成功处理
    handled: bool = True
    # 增强的系统提示词（追加到原提示词后）
    system_addon: str = ""
    # 修改后的用户消息（替换原始输入）
    rewritten_input: str = ""
    # 要注入的上下文消息（在用户消息之前）
    context_messages: list[dict] = field(default_factory=list)
    # 回复消息（如果skill自行处理完毕）
    response: str = ""
    # Skill 名称（用于前端显示）
    skill_name: str = ""
    # Skill 描述（用于前端显示）
    description: str = ""
    # 剩余输入（去掉skill命令部分）
    remaining_input: str = ""


class Skill:
    """Skill 基类。
    
    子类需要覆盖 name, triggers, description，
    并可选择覆盖 on_invoke 来自定义处理逻辑。
    """
    
    # Skill 唯一名称
    name: str = ""
    # 触发词列表，如 ["/review", "/审查"]
    triggers: list[str] = []
    # 简短描述
    description: str = ""
    
    def on_invoke(
        self,
        args: str,
        full_input: str,
        session: Session | None = None,
    ) -> SkillResult:
        """处理 skill 调用。
        
        Args:
            args: 触发词后的参数
            full_input: 完整用户输入
            session: 当前会话（可能为None）
            
        Returns:
            SkillResult 处理结果
        """
        return SkillResult(
            handled=True,
            system_addon=self.get_system_addon(args),
            rewritten_input=args if args else full_input,
        )
    
    def get_system_addon(self, args: str) -> str:
        """获取增强的系统提示词。子类应覆盖此方法。"""
        return ""
    
    def matches(self, user_input: str) -> str | None:
        """检查用户输入是否匹配此skill。
        
        Args:
            user_input: 用户输入
            
        Returns:
            匹配的触发词，不匹配返回None
        """
        stripped = user_input.strip().lower()
        for trigger in self.triggers:
            if stripped == trigger.lower() or stripped.startswith(trigger.lower() + " "):
                return trigger
        return None
