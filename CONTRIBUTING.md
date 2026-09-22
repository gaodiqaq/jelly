# 参与开发

感谢你改进 Jelly。提交改动前，请先说明问题和用户可观察到的结果，并保持改动聚焦。

## 本地环境

```powershell
uv venv .venv --python 3.10
uv pip install -e ".[dev]"
cd webui
npm ci
npm run build
cd ..
```

## 验证

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\ruff.exe check agent_shell tests scripts
cd webui
npm run build
```

涉及工作台交互时，再按 [开发与验证](docs/DEVELOPMENT.md) 运行离线预览和浏览器验收。测试应覆盖用户行为或安全边界，避免复制实现细节。

## 提交约定

- 不提交 `.env`、API Key、访问口令、会话、工作区文件、截图或用户生成作品。
- 产品文档放入 `docs/`，可重复运行的开发脚本放入 `scripts/`。
- 保持 Python 3.10 兼容；前端避免引入没有明确价值的依赖。
- UI 改动需同时检查键盘操作、窄屏布局、深浅主题和加载/空/错误状态。
- Pull Request 描述应包含触发条件、最终行为和实际运行的验证命令。
