# 果冻

AI Agent，基于 `litellm` 网关，可对接任意主流大模型（OpenAI / Anthropic / DeepSeek / Gemini / 本地 Ollama 等），可以在本地终端使用，也可以使用web界面对话。

## 特性

- **分层架构**：`ui/`（渲染交互）、`tools/`（本地执行）、`llm/`（API 通信）、`core/`（状态机），层间仅通过类型化函数签名交互，禁止跨层调用
- **工具调用循环**：bash / read / write / edit / ls / glob / grep / web_fetch / todo 共 11 个内置工具，pydantic 参数校验
- **权限控制**：默认逐个询问（支持"本次会话始终允许/拒绝"），`--yes` 全自动、`--deny` 全拒绝，只读工具可配置免审批
- **流式输出**：模型回复实时渲染；流式工具调用增量自动合并
- **会话持久化**：JSONL 保存消息历史，`--session` 恢复，`agent sessions` 列出
- **异常治理**：所有错误映射为结构化中文信息（配置/API/会话/工具各司其职），无裸 `try-except Exception`、无直接 `exit()`（除启动致命错误）

## 安装

```bash
uv venv .venv --python 3.10+
uv pip install -e .
```

## 快速开始

```bash
# 配置 API Key（任选提供商）
set OPENAI_API_KEY=sk-...        # Windows
# export OPENAI_API_KEY=sk-...   # Linux/macOS

# 交互式 REPL
agent run

# 单次任务模式
agent run "列出当前目录的文件结构并解释 main.py 的逻辑"

# 自动审批所有工具调用
agent run -y "运行测试并修复失败用例"
```

## 配置

按优先级：命令行参数 > 环境变量（`AGENT_MODEL` / `AGENT_PERMISSION` / `AGENT_CWD` / `AGENT_MAX_TURNS`）> 配置文件（`./agent_shell.yaml` 或 `~/.agent_shell/config.yaml`）> 默认值。

参考 [`config.example.yaml`](config.example.yaml) 与 [`.env.example`](.env.example)。

```bash
agent run --model anthropic/claude-sonnet-4-5 "帮我写一个快速排序"
agent run --model deepseek/deepseek-chat "这个项目里哪些文件引用了 utils？"
```

## REPL 命令

| 命令 | 说明 |
| --- | --- |
| `/help` | 帮助 |
| `/exit` `/quit` | 退出 |
| `/clear` | 清空会话（重新开始） |
| `/model <名称>` | 运行时切换模型 |
| `/auto` `/ask` | 切换自动/逐个审批模式 |
| `/session` | 当前会话 ID 与文件路径 |
| `/tools` | 列出可用工具 |

## 会话管理

```bash
agent sessions                        # 列出历史会话
agent run --session 20260731-143000   # 恢复会话继续对话
```

## Web 界面（多用户）

```bash
set AGENT_WEB_TOKEN=your-secret-token   # 必填，所有访问者共用
agent web --host 0.0.0.0 --port 8000    # 启动后浏览器打开 http://localhost:8000
```

- REST API（`/api/health`、`/api/sessions`）+ WebSocket 实时流式聊天（`/ws/{session_id}`）
- 前端为 React + Vite 构建产物（`webui/dist`），由服务端静态托管
- 所有请求需 `Authorization: Bearer <token>` 或 WS query `?token=`
- 多人各自持有独立会话，可随时新建/恢复；工具在服务器上执行（bash 默认 auto 权限）
- 本地开发：`cd webui && npm run dev`（代理到 8000 端口）

### Docker 部署

```bash
# 需要 AGENT_WEB_TOKEN / DASHSCOPE_API_KEY 环境变量
docker compose up -d --build
```

## 开发

```bash
uv pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest     # 运行测试
.venv\Scripts\ruff.exe check .          # 静态检查
.venv\Scripts\ruff.exe format .         # 格式化
```

## 目录结构

```
agent_shell/
├── types.py        # 跨层共享类型（消息/工具声明/权限决策）
├── config.py       # 配置加载（YAML + 环境变量 + CLI 覆盖）
├── errors.py       # 结构化异常体系
├── llm/            # litellm 封装：流式/非流式、异常映射、系统提示词
├── tools/          # 工具实现：注册表、bash、fs、search、web、todo
├── core/           # 会话持久化、工具执行器（权限）、Agent 状态机
├── server/         # FastAPI 服务：REST + WebSocket、会话管理、鉴权
├── ui/             # rich 渲染、REPL 输入、权限询问
└── main.py         # typer CLI 入口与依赖装配
webui/              # React + Vite 前端（构建产物 dist/ 由服务端托管）
Dockerfile          # 多阶段构建（前端打包 + 后端运行）
docker-compose.yml  # 一键部署（端口 8000）
```
