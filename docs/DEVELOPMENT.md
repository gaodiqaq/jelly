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
| `agent_shell/server/project_backup.py` | 完整项目备份、校验与独立导入 |
| `agent_shell/server/project_routes.py` | 项目 API 与成果发现 |
| `agent_shell/server/manager.py` | 会话归属、单轮配置、工具目录和恢复权限 |
| `agent_shell/server/runs.py` | 与浏览器连接解耦的任务生命周期 |
| `agent_shell/core/changes.py` | 文件快照、差异与恢复 |
| `webui/src/ProjectDialog.jsx` | 项目创建和设置 |
| `webui/src/Conversation.jsx` | 对话分组与可展开的执行记录 |
| `webui/src/Artifacts.jsx`、`useArtifacts.js` | 成果卡片与按任务刷新的文件列表 |
| `webui/src/Workspace.jsx` | 文件标签、预览、阅读目录和专注模式 |
| `webui/src/workbench.css` | 工作台布局、主题和响应式排版 |
| `webui/src/dialogs.css` | 设置、项目表单与输入区菜单排版 |
| `webui/public/fonts/` | 自托管思源黑体简体中文可变字体与 SIL OFL 许可证 |

## 自动测试

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\ruff.exe check agent_shell tests scripts
```

`tests/test_projects.py` 使用脚本模型覆盖同名文件在不同项目中的写入、预览与恢复，项目权限、目录不变性、并行任务、会话重载和多用户隔离。`tests/test_project_backup.py` 验证完整项目备份往返、会话元数据与 ZIP 目录预检、路径遍历拒绝、运行中导出保护、失败回滚和异常中断恢复。测试不请求真实模型。

## 离线界面验收

```powershell
.venv\Scripts\python.exe scripts/preview_fixture.py --port 8012
```

打开 http://127.0.0.1:8012。脚本模型预置「产品探索」项目，生成文档与 CSV；所有数据保存在忽略提交的 `scratch/workbench-preview/`。可用 `--directory` 指定另一个测试目录。服务仅监听本机，不加载真实模型或凭据。

在 `webui` 中运行 `npm ci` 后，执行 `npx playwright install chromium` 安装验收用浏览器；回到仓库根目录运行 `node scripts/check_workbench.cjs`。脚本会自动使用前端开发依赖中的 Playwright，也可通过环境变量使用现有安装：

```powershell
$env:JELLY_PLAYWRIGHT = '<Playwright 模块的绝对路径>'
$env:JELLY_CHROME = '<Chrome 可执行文件的绝对路径>'
node scripts/check_workbench.cjs
```

`JELLY_PREVIEW_URL` 可覆盖默认地址。脚本只应指向离线测试服务，因为它会创建测试项目与任务。GitHub CI 同时运行 Windows/Linux 后端回归与离线浏览器验收，并保存截图和测试服务日志。

验收包含自动新建项目目录与现有目录入口、完整项目备份下载与独立导入、提交任务后刷新、启动失败后的输入恢复、成果自动预览、交付包下载、多文件标签、Diff、静态与隔离交互网页预览、项目归档恢复、明暗主题、专注阅读及手机尺寸。截图写入 `scratch/screenshots/`，不提交生成物。

备份传输专项验收：启动预览时加 `--import-delay 2`，再运行 `node scripts/check_project_backup_ui.cjs`。它通过真实网络检查流式文件保存、上传完成后的不可取消阶段、响应断开后的自动核对、上传取消、损坏备份错误态，以及 360×800 明暗主题布局；不会使用真实项目或凭据。

可读性专项验收：运行 `node scripts/check_readability.cjs`。脚本仅接受 8012 端口的预置离线任务，检查 1440、1280、1080、390、360、900×600 和 720×450（1440×900 浏览器放大 200% 的等效 CSS 视口）。检查正文、输入与导航的实际字号，打开模型 / 权限 / Skill 菜单验证边界，滚动检查表单控件遮挡，并记录浏览器实际使用的字体。报告和截图保存在 `scratch/screenshots/readability-*`；同样纳入 CI。

字体由 `webui/public/fonts/SourceHanSansSC-VF.woff2` 提供，来源为 [Adobe 思源黑体官方仓库](https://github.com/adobe-fonts/source-han-sans)，其 SIL OFL 许可证随源码与发布包保留。中英文均使用本地思源黑体，不请求外部字体服务；字体来源、版本和摘要见 [字体资源说明](../webui/public/fonts/README.md)。新增静态资源后应运行发布包冒烟测试，确保 wheel 和源码包能完整提供字体、主题初始化脚本与页面资源：

```powershell
$env:JELLY_RUN_PACKAGE_SMOKE = '1'
.venv\Scripts\python.exe -m pytest tests/test_packaging.py -q -o addopts=
```

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
