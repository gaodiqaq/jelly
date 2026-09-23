"""Offline UI fixture: python scripts/preview_fixture.py --port 8012.

Uses a scripted model and scratch directory; never reads provider credentials.
"""

import argparse
import asyncio
import os
import time
from pathlib import Path

import uvicorn

from agent_shell.config import PermissionsConfig, Settings
from agent_shell.runtime import ProviderStore
from agent_shell.server.app import create_app
from agent_shell.server.manager import SessionManager
from agent_shell.server.projects import ProjectCreate
from agent_shell.types import AssistantMessage, ToolCall

DOCUMENT = """# 轻量级项目管理工具\n产品方案

基于小团队日常协作场景的产品设计草案

## 01　项目概述

为 5–20 人的产品与研发团队，构建一个轻量、清晰、可持续使用的项目工具。

围绕任务推进与成果交付，减少配置成本，让每个人知道下一步该做什么。

> 让小团队少维护工具，\n> 多推进工作。

## 02　用户需求

- 清楚地了解当前目标、负责人和下一步。
- 把讨论、执行记录和交付文件放在同一个任务中。
- 有需要时查看细节，日常使用时保持简单。

## 03　产品定位

以交付为中心的协作工作台。项目保存背景，任务承接行动，成果记录进展。

## 04　核心功能与优先级

| 功能 | 优先级 | 交付目标 |
| --- | --- | --- |
| 项目工作区 | P0 | 保留项目上下文与文件 |
| 任务执行记录 | P0 | 理解 Agent 做了什么 |
| 成果预览与版本 | P0 | 直接阅读、修改与恢复 |
| 复用工作方法 | P1 | 从满意的结果开始下一次 |

## 05　验证与下一步

选取三个真实任务，验证首次交付时间、返工次数和成果复用率。这是一份设计草案，尚未进行外部用户访谈。
"""

INTERACTIVE_DEMO = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><title>交互原型</title>
<style>body{font:16px system-ui;padding:40px;background:#f3f6f2;color:#23352a}
button{padding:10px 16px}</style>
<h1>交互原型</h1><p>点击次数：<strong id="count">0</strong></p>
<button id="demo" onclick="increment()">试一下</button>
<script>function increment(){const counter=document.getElementById('count');
counter.textContent=Number(counter.textContent)+1}</script>
</html>"""


class PreviewLLM:
    model = "preview/offline"

    def complete(self, messages, tools=None, *, on_token=None, **kwargs):
        time.sleep(0.6)
        if messages[-1].role == "user":
            return AssistantMessage(
                content="我会先整理目标与使用场景，再把产品方案和功能清单写进项目目录。",
                tool_calls=[
                    ToolCall(
                        id="document",
                        name="write",
                        arguments={
                            "path": "产品方案.md",
                            "content": DOCUMENT,
                            "overwrite": True,
                        },
                    ),
                    ToolCall(
                        id="comparison",
                        name="write",
                        arguments={
                            "path": "功能清单.csv",
                            "content": "功能,优先级\n项目工作区,P0\n成果预览,P0\n工作配方,P1\n",
                            "overwrite": True,
                        },
                    ),
                    ToolCall(
                        id="prototype",
                        name="write",
                        arguments={
                            "path": "交互原型.html",
                            "content": INTERACTIVE_DEMO,
                            "overwrite": True,
                        },
                    ),
                ],
            )
        content = (
            "初步方案已完成。核心方向是：**让小团队少维护工具，多推进工作。**\n\n"
            "我已整理好产品方案与功能清单，可以直接在右侧阅读，再继续调整。"
        )
        if on_token:
            on_token(content)
        return AssistantMessage(content=content)


class PreviewManager(SessionManager):
    """A deterministic preflight failure for the prompt-recovery browser check."""

    def _build_agent(self, session, *args, **kwargs):
        if session.title.startswith("模拟启动失败"):
            raise RuntimeError("离线验收：模型尚未就绪")
        return super()._build_agent(session, *args, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8012)
    parser.add_argument("--directory", type=Path, default=Path("scratch/workbench-preview"))
    parser.add_argument("--token", help="Optional access token for testing the sign-in gate")
    args = parser.parse_args()
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=True)
    for name in ("research", "design"):
        (root / name).mkdir(exist_ok=True)
    os.environ.pop("AGENT_WEB_USERS", None)
    os.environ.pop("AGENT_WEB_TOKEN", None)
    os.environ["AGENT_PROJECTS_ROOT"] = str(root / "managed")
    settings = Settings(
        cwd=root, session_dir=root / "sessions", permissions=PermissionsConfig(default="auto")
    )
    store = ProviderStore(root / "runtime.yaml")
    store.set_model("preview/offline")
    manager = PreviewManager(settings, PreviewLLM(), store=store)
    if not manager.projects.list():
        project = manager.projects.save(
            ProjectCreate(
                name="产品探索",
                cwd=str(root / "research"),
                permission="auto",
            )
        )
        session = manager.create_session(project["id"])

        async def seed():
            async def emit(_):
                pass

            await manager.run_agent(
                session.session_id,
                "整理小团队项目管理工具的产品方案，明确产品定位、核心功能和 MVP 路线。",
                emit,
            )

        asyncio.run(seed())
    uvicorn.run(
        create_app(settings, manager, api_token=args.token, store=store),
        host="127.0.0.1",
        port=args.port,
    )


if __name__ == "__main__":
    main()
