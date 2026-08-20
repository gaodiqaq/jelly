"""Jelly Skills 系统：可扩展的命令式技能。

使用方式：
    /skillname args...
    
内置skills：
    /review    - 代码审查
    /fix       - Bug修复
    /refactor  - 代码重构
    /explain   - 代码解释

安装skill：
    POST /api/skills/install  {"url": "https://..."}
    POST /api/skills/reload   # 重新加载
"""

from agent_shell.skills.registry import SkillRegistry, get_global_registry, list_skills
from agent_shell.skills.installer import (
    install_skill_from_url,
    install_skill_from_definition,
    uninstall_skill,
    fetch_skill_from_url,
    parse_skill_markdown,
    SkillDefinition,
)

__all__ = [
    "SkillRegistry",
    "get_global_registry",
    "list_skills",
    "install_skill_from_url",
    "install_skill_from_definition",
    "uninstall_skill",
    "fetch_skill_from_url",
    "parse_skill_markdown",
    "SkillDefinition",
]
