# --- 前端构建 ---
FROM node:22-alpine AS webui
WORKDIR /app
COPY webui/package.json webui/package-lock.json* ./
RUN npm install
COPY webui/ ./
RUN npm run build

# --- 后端运行 ---
FROM python:3.12-slim AS runtime
WORKDIR /app
COPY pyproject.toml README.md ./
COPY agent_shell/ ./agent_shell/
RUN pip install --no-cache-dir .
COPY --from=webui /app/dist /app/webui/dist
EXPOSE 8000
ENV AGENT_CWD=/workspace
VOLUME ["/workspace"]
CMD ["agent", "web", "--host", "0.0.0.0", "--port", "8000"]
