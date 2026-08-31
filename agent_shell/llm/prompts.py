"""系统提示词构建。"""

from __future__ import annotations

import platform
from datetime import date
from pathlib import Path

DEFAULT_SYSTEM_PROMPT = """\
你是果冻，一个运行在本地终端中的 AI 编程助手，类似于 Claude Code。
你通过与用户对话、调用工具来帮助用户完成编程与系统管理任务。

## 工作方式

1. 先理解用户意图，必要时拆解为任务清单（todo_add / todo_done）。
2. 需要获取信息或执行操作时，调用对应工具；不要臆测文件内容或命令结果。
3. 只读操作（read/ls/glob/grep）可以直接执行；修改性操作（bash/write/edit）
   会经过用户审批，被拒绝时不要重试相同操作，请换一种方式或询问用户。
4. 多步骤任务逐步推进，完成一步后用工具结果驱动下一步，不要一次性幻想全部结果。
5. 任务完成后给出简洁的中文总结（除非用户要求使用其他语言）。

## 行为准则

- 工具参数必须符合 schema；grep 的正则非法、edit 的 old_string 不匹配等
  都会返回错误，请根据错误信息修正参数重试。
- bash 输出可能被截断；需要完整内容时，用 read / grep 等专用工具定向读取。
- 不要执行可能造成不可逆破坏的命令（rm -rf、格式化等），除非用户明确要求。
- 无法完成任务时，明确说明原因和可行的替代方案。
- 禁止编造工具结果或文件内容。

## 环境

- 当前工作目录（cwd）: {cwd}
- 操作系统: {os}
- 当前日期: {today}
- 模型: {model}
"""


def build_system_prompt(cwd: Path, model: str, skills: list[dict] | None = None) -> str:
    """构建默认系统提示词（含环境信息与可用技能清单）。

    Args:
        cwd: 当前工作目录。
        model: 当前模型名。
        skills: 可用技能列表（[{name, description, triggers}]）；
            提供时追加"可用技能"段落，模型可根据用户意图自主激活。

    Returns:
        完整系统提示词文本。
    """
    prompt = DEFAULT_SYSTEM_PROMPT.format(
        cwd=cwd,
        os=platform.platform(),
        today=date.today().isoformat(),
        model=model,
    )
    if skills:
        prompt += build_skill_catalog(skills)
    return prompt


def build_skill_catalog(skills: list[dict]) -> str:
    """构建技能清单段落（注入系统提示词，供模型自主触发）。

    Args:
        skills: 技能列表（[{name, description, triggers, resource_dir}]）。

    Returns:
        "## 可用技能" markdown 段落。
    """
    lines = ["\n## 可用技能（Skills）\n"]
    lines.append(
        "当用户请求与下列技能匹配时，先激活该技能：用 read 工具读取其"
        "SKILL.md（及 references/ 辅助文档），然后严格按其指令执行。"
    )
    for s in skills:
        name = s.get("name", "")
        desc = s.get("description", "")
        triggers = s.get("triggers") or []
        resource_dir = s.get("resource_dir", "")
        lines.append(f"\n### {name}")
        if desc:
            lines.append(desc)
        if triggers:
            lines.append(f"- 触发词: {' / '.join(triggers)}")
        if resource_dir:
            lines.append(f"- 技能文件: {resource_dir}/SKILL.md")
    return "\n".join(lines)
