"""agent-shell: 类似 Claude Code 的本地终端 Agent。

分层架构:
- ui/     终端渲染与交互
- tools/  本地工具执行
- llm/    大模型 API 通信
- core/   会话状态与 Agent 状态机
"""

__version__ = "0.1.0"
