# 开发与验证

## 本地运行

在仓库根目录安装 Python 依赖，在 `webui` 安装并构建前端：

```powershell
uv venv .venv
uv pip install -e ".[dev]"
cd webui
npm ci
npm run build
cd ..
.venv\Scripts\agent.exe web --host 127.0.0.1 --port 8000
```

前端资源更新后刷新浏览器；后端代码更新后重启服务。运行数据放在配置的 `session_dir`，不应提交到仓库。

## 源码分工

| 位置 | 职责 |
| --- | --- |
| `agent_shell/server/projects.py` | 用户项目目录与设置的持久化 |
| `agent_shell/server/project_routes.py` | 项目 API 与成果发现 |
| `agent_shell/server/manager.py` | 会话归属、单轮配置、工具目录和恢复权限 |
| `agent_shell/server/runs.py` | 与浏览器连接解耦的任务生命周期 |
| `agent_shell/core/changes.py` | 文件快照、差异与恢复 |
| `webui/src/ProjectDialog.jsx` | 项目创建和设置 |
| `webui/src/Conversation.jsx` | 对话分组与可展开的执行记录 |
| `webui/src/Artifacts.jsx`、`useArtifacts.js` | 成果卡片与按任务刷新的文件列表 |
| `webui/src/Workspace.jsx` | 文件标签、预览、阅读目录和专注模式 |
| `webui/src/workbench.css` | 工作台布局、主题和响应式排版 |

## 自动测试

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\ruff.exe check agent_shell tests scripts
```

`tests/test_projects.py` 使用脚本模型覆盖同名文件在不同项目中的写入、预览与恢复，项目权限、目录不变性、并行任务、会话重载和多用户隔离。测试不请求真实模型。

## 离线界面验收

```powershell
.venv\Scripts\python.exe scripts/preview_fixture.py --port 8012
```

打开 http://127.0.0.1:8012。脚本模型预置「产品探索」项目，生成文档与 CSV；所有数据保存在忽略提交的 `scratch/workbench-preview/`。可用 `--directory` 指定另一个测试目录。服务仅监听本机，不加载真实模型或凭据。

在另一个终端运行浏览器脚本。需要已安装 Playwright 与 Chromium；也可通过环境变量使用现有安装：

```powershell
$env:JELLY_PLAYWRIGHT = '<Playwright 模块的绝对路径>'
$env:JELLY_CHROME = '<Chrome 可执行文件的绝对路径>'
node scripts/check_workbench.cjs
```

若 Playwright 可通过 Node 模块解析找到，且已安装对应浏览器，可省略环境变量。`JELLY_PREVIEW_URL` 可覆盖默认地址。脚本只应指向离线测试服务，因为它会创建测试项目与任务。

验收包含自动新建项目目录与现有目录入口、提交任务后刷新、启动失败后的输入恢复、成果自动预览、交付包下载、多文件标签、Diff、静态与隔离交互网页预览、项目归档恢复、明暗主题、专注阅读及手机尺寸。截图写入 `scratch/screenshots/`，不提交生成物。

访问口令流程可用独立端口验证：

```powershell
.venv\Scripts\python.exe scripts/preview_fixture.py --port 8013 --token jelly-preview-token
$env:JELLY_PREVIEW_URL = 'http://127.0.0.1:8013'
$env:JELLY_PREVIEW_TOKEN = 'jelly-preview-token'
node scripts/check_access_gate.cjs
```

脚本会验证错误口令、正确登录以及请求 URL 不携带访问口令。

## 仓库整理约定

产品说明放 `docs/`，可重复运行的辅助工具放 `scripts/`，功能测试放 `tests/`。运行数据、临时文件和截图放 `scratch/`。`webui/dist/` 作为 Python 发布包的内置工作室资源随源码提交；前端改动后必须重新构建并一并更新。依赖目录保持忽略。用户生成的独立作品不属于产品源码，整理时不要覆盖或删除。
