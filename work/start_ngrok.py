"""
使用 pyngrok 启动内网穿透
需要先安装: pip install pyngrok
"""
from pyngrok import ngrok
import os
from dotenv import load_dotenv

# 配置 authtoken（从 https://dashboard.ngrok.com/get-started/your-authtoken 获取）
# 方式1: 直接设置（不推荐提交到 git）
# ngrok.set_auth_token("你的token")

load_dotenv()  # 这样 Python 就会自动读取 .env 文件里的配置了

# 方式2: 从环境变量读取（推荐）
auth_token = os.getenv("NGROK_AUTH_TOKEN")
if auth_token:
    ngrok.set_auth_token(auth_token)
else:
    print("⚠️ 未设置 NGROK_AUTH_TOKEN 环境变量")
    print("请从 https://dashboard.ngrok.com/get-started/your-authtoken 获取 token")
    print("然后设置环境变量: set NGROK_AUTH_TOKEN=你的token")
    print("或者直接在代码中设置（不推荐）")
    exit(1)

# 启动穿透，指向本地 8000 端口
public_url = ngrok.connect(8000)
print(f"\n✅ 公网访问地址: {public_url}")
print("按 Ctrl+C 停止...")

# 保持运行
try:
    # 正确的 API 用法：获取 ngrok 进程并阻塞等待
    ngrok.get_ngrok_process().proc.wait()
except KeyboardInterrupt:
    print("\n正在关闭...")
    # public_url 是一个 NgrokTunnel 对象，取其 .public_url 属性作为字符串传入
    ngrok.disconnect(public_url.public_url) 
    ngrok.kill()
