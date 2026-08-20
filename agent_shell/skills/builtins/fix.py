"""Bug修复 Skill。"""

from __future__ import annotations

from agent_shell.skills.base import Skill, SkillResult


class FixSkill(Skill):
    """Bug修复：分析错误并给出修复方案。"""
    
    name = "fix"
    triggers = ["/fix", "/修复", "/bugfix", "/debug"]
    description = "Bug修复：分析错误并给出修复方案"
    
    def get_system_addon(self, args: str) -> str:
        bug_desc = f"问题描述: {args}" if args else "用户会提供错误信息或异常堆栈"
        return f"""
---

## 🐛 Bug修复模式已激活

{bug_desc}

### 修复流程

1. **理解问题**：复述你理解的问题现象
2. **定位根因**：分析错误堆栈/日志，找到根本原因
3. **制定方案**：提出2-3种可能的修复方案，说明利弊
4. **执行修复**：选择最佳方案并实施修复
5. **验证测试**：提供验证方法确保修复有效

### 注意事项

- 修复前先用 fs_read 确认当前代码状态
- 修改代码时使用 fs_write，保持最小改动
- 修复完成后运行相关测试验证
"""
