"""代码重构 Skill。"""

from __future__ import annotations

from agent_shell.skills.base import Skill, SkillResult


class RefactorSkill(Skill):
    """代码重构：改善代码结构、消除坏味道。"""
    
    name = "refactor"
    triggers = ["/refactor", "/重构", "/cleanup"]
    description = "代码重构：改善代码结构、消除坏味道"
    
    def get_system_addon(self, args: str) -> str:
        target = f"重构目标: {args}" if args else "用户指定的代码区域"
        return f"""
---

## 🔧 代码重构模式已激活

{target}

### 重构原则

1. **小步前进**：每次只做一个小的改动
2. **保持行为**：重构后功能不变
3. **测试保护**：确保有测试覆盖或手动验证

### 常见重构手法

- 提取函数/方法
- 重命名变量/函数
- 消除重复代码
- 简化条件表达式
- 引入设计模式
- 拆分大类/大函数

### 输出格式

1. **重构前**：当前代码的问题
2. **重构步骤**：按顺序的改动步骤
3. **重构后**：改进后的代码
4. **收益**：重构带来的好处

先列出重构计划，确认后再执行。
"""
