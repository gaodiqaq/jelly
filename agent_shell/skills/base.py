"""Skill 基类与数据结构。

Skill 是一个可复用的"能力包"，包含：
- 名称与触发词（斜杠命令 + 自然语言触发词）
- 增强的系统提示词（system_addon）
- 可选的辅助资源目录（references / scripts / assets，供模型用 read 工具读取）

两种 Skill 形态:
- :class:`Skill`: Python 类实现（内置 skill，如 review/fix），可覆盖 ``on_invoke``
  自定义处理逻辑
- :class:`MarkdownSkill`: 从标准 ``SKILL.md`` 加载的声明式 skill（Claude Code
  生态格式），无需写代码，安装后即用
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
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
    # 技能资源目录（SKILL.md 所在目录，模型可用 read 读取 references 等）
    resource_dir: str = ""


class Skill:
    """Skill 基类（Python 实现形态）。

    子类需要覆盖 name, triggers, description，
    并可选择覆盖 on_invoke 来自定义处理逻辑。
    """

    # Skill 唯一名称
    name: str = ""
    # 触发词列表，如 ["/review", "/审查"]（斜杠命令或自然语言短语均可）
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


class MarkdownSkill(Skill):
    """从标准 ``SKILL.md`` 加载的声明式 skill。

    标准格式（Claude Code 生态）:
    ```markdown
    ---
    name: chinese-novelist
    description: |
      多行描述...
    metadata:
      trigger: 自然语言触发词（逗号分隔）
    ---
    # 正文（markdown 指令，可包含 references/xxx.md 相对引用）
    ```

    安装器将 SKILL.md 连同 references/scripts/assets 一起复制到
    ``~/.agent_shell/skills/<name>/``，本类负责把正文注入系统提示词，
    并告知模型辅助文档的绝对路径（模型可用 read 工具按需读取）。
    """

    def __init__(
        self,
        name: str,
        description: str,
        triggers: list[str],
        body: str,
        resource_dir: str | Path = "",
    ) -> None:
        self.name = name
        self.description = description
        self.triggers = triggers or []
        self._body = body
        self._resource_dir = str(resource_dir)

    def get_system_addon(self, args: str) -> str:
        """返回 SKILL.md 正文，并附加辅助文档路径说明。"""
        addon = self._body.strip()
        if self._resource_dir:
            addon += (
                "\n\n## 技能辅助文档\n"
                f"本技能的资源目录: {self._resource_dir}\n"
                "正文中引用的 references/ scripts/ assets/ 等文件都位于该目录下，"
                "需要时请用 read 工具读取对应文件获取详细指令。"
            )
        return addon

    def on_invoke(
        self,
        args: str,
        full_input: str,
        session: Session | None = None,
    ) -> SkillResult:
        return SkillResult(
            handled=True,
            system_addon=self.get_system_addon(args),
            skill_name=self.name,
            description=self.description,
            resource_dir=self._resource_dir,
        )
