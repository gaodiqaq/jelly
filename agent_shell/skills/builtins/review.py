"""代码审查 Skill。"""

from __future__ import annotations

from agent_shell.skills.base import Skill, SkillResult


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


class FixSkill(Skill):
    """Bug修复 Skill。"""
    
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


class RefactorSkill(Skill):
    """代码重构 Skill。"""
    
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


class ExplainSkill(Skill):
    """代码解释 Skill。"""
    
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
