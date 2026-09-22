# --- 前端构建 ---
FROM node:26-alpine AS webui
WORKDIR /app
COPY webui/package.json webui/package-lock.json* ./
RUN npm ci
COPY webui/ ./
RUN npm run build

# --- 后端运行 ---
FROM python:3.12-slim AS runtime
WORKDIR /app
COPY pyproject.toml README.md ./
COPY agent_shell/ ./agent_shell/
COPY --from=webui /app/dist /app/webui/dist
RUN pip install --no-cache-dir .
RUN useradd --create-home --uid 10001 jelly \
  && mkdir -p /workspace /home/jelly/.agent_shell \
  && chown -R jelly:jelly /workspace /home/jelly
EXPOSE 8000
ENV AGENT_CWD=/workspace \
    HOME=/home/jelly \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
VOLUME ["/workspace", "/home/jelly/.agent_shell"]
USER jelly
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
CMD ["agent", "web", "--host", "0.0.0.0", "--port", "8000"]
