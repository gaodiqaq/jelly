"""代码审查 Skill。"""

from __future__ import annotations

from agent_shell.skills.base import Skill


class ReviewSkill(Skill):
    """代码审查：分析代码质量、安全性、性能等问题。"""

    name = "review"
    triggers = ["/review", "/审查", "/code-review"]
    description = "代码审查：检查代码质量、安全性、性能、可维护性"

    def get_system_addon(self, args: str) -> str:
        target = f"用户指定的目标: {args}" if args else "用户当前查看的代码或项目"
        return f"""
---

## 🔍 代码审查模式已激活

你现在是一级代码审查专家。{target}

### 审查维度

1. **正确性**：逻辑错误、边界条件、并发安全
2. **安全性**：注入漏洞、权限控制、敏感信息泄露
3. **性能**：时间复杂度、内存泄漏、N+1查询
4. **可维护性**：命名规范、代码重复、过度设计
5. **可测试性**：是否易于单元测试、依赖是否可mock

### 输出格式

对每个问题：
- 📍 **位置**：文件名+行号
- 🔴 **严重性**：Critical / High / Medium / Low
- 📝 **问题**：简洁描述
- 💡 **建议**：修复代码示例

先总结整体评价，再按严重性降序列出问题。
"""
