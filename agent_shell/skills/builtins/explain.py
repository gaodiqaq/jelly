"""代码解释 Skill。"""

from __future__ import annotations

from agent_shell.skills.base import Skill, SkillResult


class ExplainSkill(Skill):
    """代码解释：解释代码功能、逻辑、架构。"""
    
    name = "explain"
    triggers = ["/explain", "/解释", "/what", "/how"]
    description = "代码解释：解释代码功能、逻辑、架构"
    
    def get_system_addon(self, args: str) -> str:
        target = f"解释目标: {args}" if args else "用户查看的代码或概念"
        return f"""
---

## 📖 代码解释模式已激活

{target}

### 解释层次

1. **一句话概括**：这段代码做什么
2. **核心逻辑**：关键步骤和算法
3. **数据流转**：输入如何变成输出
4. **关键细节**：值得注意的实现细节
5. **潜在问题**：可能的边界情况或陷阱

### 表达要求

- 用通俗易懂的语言
- 复杂概念用类比说明
- 关键代码片段逐行注释
- 必要时用图表辅助说明
- 根据用户水平调整深度
"""
