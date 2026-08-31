# 果冻

AI Agent，基于 `litellm` 网关，可对接任意主流大模型（OpenAI / Anthropic / DeepSeek / Gemini / 本地 Ollama 等），可以在本地终端使用，也可以使用web界面对话。

## 特性

- **分层架构**：`ui/`（渲染交互）、`tools/`（本地执行）、`llm/`（API 通信）、`core/`（状态机），层间仅通过类型化函数签名交互，禁止跨层调用
- **统一 Provider 系统**：支持 OpenAI / Anthropic / DeepSeek / Gemini / 本地 Ollama / vLLM 等任意 litellm 兼容提供商，Web 端设置面板可动态添加/删除/切换，无需重启
- **Skill 系统**：可插拔技能框架，支持 `/review` `/fix` `/refactor` `/explain` `/chinese-novelist` 等斜杠命令，每个 Skill 拥有专属系统提示词，自动注入对话上下文；支持动态安装/卸载/热重载
- **工具调用循环**：bash / read / write / edit / ls / glob / grep / web_fetch / todo 共 11 个内置工具，pydantic 参数校验，调用结果展示在文字回答上方，完成后自动标记"已完成"
- **权限控制**：只读 / 手动审批 / 自动授权三档模式，界面与命令行均可随时切换；手动审批时 Agent 暂停等待确认，支持"本会话始终允许/拒绝"，只读工具可配置免审批，无审批渠道时修改性操作 fail-closed 拒绝
- **流式输出**：模型回复实时渲染；流式工具调用增量自动合并，思考过程可折叠
- **会话持久化**：JSONL 保存消息历史，`--session` 恢复，`agent sessions` 列出
- **运行时热切换**：REPL 中 `/model`、`/apikey`、`/baseurl` 即时切换并写回 `~/.agent_shell/config.yaml`，Web 端可通过设置面板（模型 / 各提供商 Key / Base URL / 连通性测试）完成相同操作
- **无认证模式**：Web 界面默认无需登录，打开即用；可选配置 `AGENT_WEB_USERS` 启用多用户隔离
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
| `/model <名称>` | 运行时切换模型并持久化 |
| `/apikey <提供商> [密钥]` | 设置 API Key；不带密钥则查看当前掩码 |
| `/baseurl <提供商> <URL>` | 设置提供商 Base URL |
| `/providers` | 列出已配置的提供商（Key 掩码展示） |
| `/config` | 查看完整运行时配置摘要 |
| `/auto` `/ask` | 切换自动/逐个审批模式 |
| `/session` | 当前会话 ID 与文件路径 |
| `/tools` | 列出可用工具 |
| `/review` | 激活代码审查 Skill |
| `/fix` | 激活 Bug 修复 Skill |
| `/refactor` | 激活代码重构 Skill |
| `/explain` | 激活代码解释 Skill |
| `/chinese-novelist` | 激活中文小说创作 Skill（别名 `/中文小说` `/长篇小说`） |
| `/skills` | 列出所有可用 Skill |

## 会话管理

```bash
agent sessions                                           # 列出历史会话
agent run --session 20260731-143000-a1b2                 # 恢复会话继续对话
```

## Web 界面

```bash
agent web --host 0.0.0.0 --port 8000    # 启动后浏览器打开 http://localhost:8000
```

默认**无需登录**，打开即可使用。

### 界面特性

- **「乌梅冻」深色主题**：深乌梅底 + 蜜桃冻强调色，消息气泡、工具卡片、弹层全套统一视觉；半透明"果冻方块"作为品牌标识与 AI 头像，Agent 思考时会轻轻颤动
- **工作台面板**：右上角文件夹按钮开关，展示工作区文件树（懒加载展开）；点文件即预览——Markdown 渲染成排版文章、代码等宽展示、图片直接显示、二进制提供原始文件链接；面板可拖拽调宽（320–720px），开合状态与宽度自动记忆，窄屏自动变为覆盖抽屉
- **点击即预览**：消息中的文件路径（工具卡片参数与回复正文里的路径）自动识别为链接，点击即在右侧工作台打开对应文件
- **权限模式选择器**：输入框左下角随时切换 **只读 / 手动审批 / 自动授权**；手动审批模式下，修改性工具执行前对话内弹出「权限确认」卡片（允许 / 拒绝 / 本会话始终允许 / 始终拒绝），Agent 暂停等待决策，期间可点停止，超时自动拒绝
- **Skill 快捷入口**：输入框左下角闪电按钮，弹出 Skill 列表点击即激活
- 生成中可点击停止按钮中断；右上角展示每轮 token 用量与缓存命中率

### 可选：启用多用户隔离

```bash
# 多用户模式：各用户会话隔离（推荐多人使用场景）
set AGENT_WEB_USERS=alice:token1,bob:token2     # Windows
export AGENT_WEB_USERS=alice:token1,bob:token2  # Linux/macOS
```

配置 `AGENT_WEB_USERS` 后，各用户需要输入自己的 token 登录，拥有独立的会话与待办目录，互不可见。

### 技术说明

- REST API（`/api/health`、`/api/sessions`、`/api/config`、`/api/permissions`、`/api/files`、`/api/file`）+ WebSocket 实时流式聊天（`/ws/{session_id}`，支持 `stop` 与 `approval_decision` 上行消息）
- 工作区文件 API 为只读，路径解析后强制限制在 Agent 工作目录内（防路径穿越）
- 前端为 React + Vite 构建产物（`webui/dist`），由服务端静态托管
- 配置了 `AGENT_WEB_USERS` 时所有请求需 `Authorization: Bearer <token>`；WS 则用 query `?token=`；未配置则不校验
- 右上角设置面板：切换模型、填写任意提供商 Key / Base URL、连通性测试，保存后写回 `~/.agent_shell/config.yaml`，运行时立即生效
- 本地开发：`cd webui && npm run dev`（代理到 8000 端口）

### Docker 部署

```bash
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
├── runtime.py      # 运行时配置存储：providers Key/Base URL 热切换与持久化
├── errors.py       # 结构化异常体系
├── llm/            # litellm 封装：流式/非流式、异常映射、系统提示词
├── tools/          # 工具实现：注册表、bash、fs、search、web、todo
├── skills/         # Skill 系统：基类、注册表、安装器、内置 Skill
│   └── builtins/   # 内置 Skill：review、fix、refactor、explain、chinese-novelist
├── core/           # 会话持久化、工具执行器（权限模式与审批）、Agent 状态机（支持停止）
├── server/         # FastAPI 服务：REST + WebSocket、会话管理、多用户隔离、鉴权、权限审批、工作台文件 API
├── ui/             # rich 渲染、REPL 输入、权限询问
└── main.py         # typer CLI 入口与依赖装配
webui/              # React + Vite 前端：乌梅冻主题、工作台面板、权限选择器（dist/ 由服务端托管）
Dockerfile          # 多阶段构建（前端打包 + 后端运行）
docker-compose.yml  # 一键部署（端口 8000）
```
