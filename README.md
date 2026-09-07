# 果冻

**Jelly** — 本地优先的 AI Agent，支持 Web 界面与终端两种用法。

Web 端已升级为 **Jelly Studio**：成果与版本、文件差异恢复、工作配方、明暗主题，以及刷新后可恢复查看的后台任务。详见 [工作室使用说明与能力边界](STUDIO.md)。

基于 `litellm` 网关，可对接任意主流大模型（OpenAI / Anthropic / DeepSeek / Gemini / 本地 Ollama / vLLM 等）。自带完整工具链（文件、Bash、搜索、网页抓取）、可插拔 Skill 技能系统、多会话持久化与权限控制。

## 快速开始（Web 端）

启动 Web 服务后，在浏览器里打开工作室：

```bash
# 启动 Web 服务（默认监听 8000 端口）
.\venv\Scripts\agent.exe web --host 0.0.0.0 --port 8000
```

打开浏览器访问 **http://localhost:8000** 即可开始使用。默认无需登录。

### 启动 / 停止脚本

仓库提供一键脚本（Windows）：

```bash
start_jelly.bat      # 双击或命令行运行：启动 Web 服务
stop_jelly.bat       # 停止所有监听 8000 端口的 jelly 进程
```

> 注意：启动前先确认没有已运行的实例（`netstat -ano | findstr ":8000.*LISTENING"`），避免端口冲突。

### Jelly Studio

Jelly Studio 把 Agent 从“聊天窗口”变成可持续打磨成果的工作空间：

- **成果与版本**：记录 `write/edit` 的修改前后内容，查看文本 Diff，并在文件未被后续改动时恢复。
- **任务可恢复**：运行状态独立保存，刷新或切换页面后仍可查看进行中的任务；停止会等待执行器确认。
- **工作配方**：把一次满意的工作方法整理成可编辑、可复用的配方，并保留版本号。
- **作品预览**：右侧工作区支持文件树、Markdown、代码、图片和隔离的 HTML 预览。
- **双主题**：暖白工作模式与乌梅深色模式，主题选择保存在当前浏览器。

文件恢复只覆盖工作区内可建立快照的 `write/edit` 操作；Bash、网络请求和外部服务等副作用不会被假定为可撤回。完整边界见 [`STUDIO.md`](STUDIO.md)。

### 其他 Web 功能

- **「果冻」品牌界面**：果冻方块作为品牌标识与 AI 头像，状态反馈保持轻量
- **工作台面板**：右侧文件面板展示工作区文件树，点击文件即可预览（Markdown 渲染、代码等宽、图片直显、二进制提供原始下载）
- **点击即预览**：消息中的文件路径自动识别为链接，点击即在面板中打开对应文件
- **Skill 快捷入口**：输入框左下角 ⚡ 按钮弹出技能列表，点击即激活
- **权限模式选择器**：随时切换 只读 / 手动审批 / 自动授权；手动审批时工具执行前弹出确认卡片
- **工作区切换**：设置面板可修改工作目录（`cwd`），切换后立即对下一轮对话生效并持久化
- **运行时热切换**：设置面板可切换模型、配置各提供商 Key / Base URL、测试连通性，无需重启
- **流式输出**：模型回复实时渲染，token 用量与缓存命中率实时展示
- **多用户隔离**：设置 `AGENT_WEB_USERS` 后各用户拥有独立会话，登录可见

## Skill 技能系统

Skill 是可插拔的技能包：安装后，用户说出触发词（斜杠命令或自然语言）即可激活专属工作流，模型会按技能指令执行（如代码审查、小说创作、文档分析等）。

```bash
agent skills list                              # 列出所有技能
agent skills install https://github.com/xxx/yyy  # 从 GitHub 仓库安装标准 SKILL.md 技能
agent skills install ./本地技能目录             # 从本地目录安装
agent skills remove 技能名                      # 卸载
```

- **安装格式**：支持 Claude Code 生态标准的 `SKILL.md`（含 `references/`、`scripts/` 资源目录），也兼容旧版 `name/description/triggers` 格式
- **触发方式**：斜杠命令（`/技能名`）或自然语言（`使用 技能名 帮我…`、front matter 声明的触发短语）
- **自主触发**：技能清单注入系统提示词，模型遇到匹配请求时会自行读取技能文档并按指令执行
- **Web 端**：⚡ 按钮弹出技能列表，点击即激活；设置面板可看到全部技能
- 内置技能示例：`review`（代码审查）、`fix`（Bug 修复）、`refactor`（重构）、`explain`（讲解）、`chinese-novelist`（中文小说创作）

## 终端用法

```bash
# 交互式 REPL
agent

# 单次任务模式
agent run "列出当前目录结构并解释 main.py 的逻辑"

# 自动审批所有工具调用
agent run -y "运行测试并修复失败用例"
```

### REPL 常用命令

| 命令 | 说明 |
| --- | --- |
| `/cwd <路径>` | 切换工作区（立即生效并持久化），不带参数查看当前 |
| `/model <名称>` | 切换模型并持久化 |
| `/apikey <提供商> [密钥]` | 配置 API Key |
| `/baseurl <提供商> <URL>` | 配置 Base URL |
| `/skills` | 列出所有技能 |
| `/tools` | 列出可用工具 |
| `/auto` `/ask` | 切换自动授权 / 手动审批 |
| `/clear` | 清空会话（重新开始） |
| `/session` | 查看当前会话信息 |
| `/help` | 帮助 |

## 特性

- **分层架构**：`ui/`（渲染交互）、`tools/`（本地执行）、`llm/`（API 通信）、`core/`（状态机）、`server/`（Web 服务），层间仅通过类型化签名交互
- **统一 Provider 系统**：支持任意 litellm 兼容提供商，Web 设置面板 / 终端命令均可动态添加、切换、持久化，无需重启
- **工具调用循环**：`bash` / `read` / `write` / `edit` / `ls` / `glob` / `grep` / `web_fetch` / `todo` / `open` 共 12 个内置工具，pydantic 参数校验，调用结果实时展示
- **open 工具**：用系统默认程序打开文件 / 目录 / URL（非阻塞），解决"帮我打开某个文件"类需求；不要用 bash 跑 GUI 程序（会阻塞超时）
- **权限控制**：只读 / 手动审批 / 自动授权三档，支持"本会话始终允许/拒绝"，无审批渠道时修改性操作 fail-closed
- **会话持久化**：JSONL 保存历史，`--session` 恢复，`agent sessions` 列出
- **工作区切换**：CLI `/cwd` 与 Web 设置面板均可运行时切换工作目录，工具相对路径与系统提示词同步更新
- **异常治理**：所有错误映射为结构化中文信息，无裸 try-except

## 安装

```bash
uv venv .venv --python 3.10+
uv pip install -e .
```

## 配置

按优先级：命令行参数 > 环境变量（`AGENT_MODEL` / `AGENT_PERMISSION` / `AGENT_CWD` / `AGENT_MAX_TURNS`）> 配置文件（`./agent_shell.yaml` 或 `~/.agent_shell/config.yaml`）> 默认值。

参考 [`config.example.yaml`](config.example.yaml) 与 [`.env.example`](.env.example)。

## 会话管理

```bash
agent sessions                                    # 列出历史会话
agent run --session 20260731-143000-a1b2          # 恢复会话继续对话
```

## Docker 部署

```bash
docker compose up -d --build
```

## 开发

```bash
uv pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest     # 运行测试
.venv\Scripts\ruff.exe check .          # 静态检查
```

前端本地开发：`cd webui && npm run build`（构建产物由服务端静态托管到 `/`）。

## 目录结构

```
agent_shell/
├── types.py        # 跨层共享类型（消息/工具声明/权限决策）
├── config.py       # 配置加载（YAML + 环境变量 + CLI 覆盖，含 cwd）
├── runtime.py      # 运行时配置：providers / model / cwd 热切换与持久化
├── errors.py       # 结构化异常体系
├── llm/            # litellm 封装：流式/非流式、异常映射、系统提示词（含技能清单注入）
├── tools/          # 工具实现：注册表、bash、fs、search、web、todo、open
│   └── open_file.py  # open 工具：系统默认程序打开文件/目录/URL
├── skills/         # Skill 系统：基类、注册表、安装器（标准 SKILL.md）、内置 Skill
│   └── builtins/   # 内置 Skill
├── core/           # Agent 状态机、会话、权限执行器、文件变更记录
├── server/         # FastAPI：REST + WebSocket、任务运行、配方、会话与工作区 API
├── ui/             # rich 渲染、REPL 输入、权限询问
└── main.py         # typer CLI 入口
webui/              # React + Vite 前端（Jelly Studio，dist/ 由服务端托管）
STUDIO.md           # 工作室能力、数据结构与边界说明
Dockerfile          # 多阶段构建
docker-compose.yml  # 一键部署（端口 8000）
start_jelly.bat     # 启动 Web 服务脚本
stop_jelly.bat      # 停止 Web 服务脚本
```
