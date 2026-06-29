"""Entry point: python -m gemini_web2api"""
import argparse
import os

from .config import CONFIG, load_config, find_config
from .models import MODELS
from .gemini import HAS_HTTPX
from .server import GeminiHandler, ThreadedServer
from . import __version__

from .admin import set_config_file
from .account_pool import set_account_file, init_pool_from_cookie


def main():
    parser = argparse.ArgumentParser(description="Gemini 网页转 OpenAI API 服务")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--cookie-file", type=str, default=None)
    parser.add_argument("--proxy", type=str, default=None, help="HTTP 代理，例如 http://127.0.0.1:7890")
    parser.add_argument("--version", action="version", version=f"gemini-web2api {__version__}")
    args = parser.parse_args()

    config_path = args.config or os.environ.get("GEMINI_WEB2API_CONFIG") or find_config()
    if config_path:
        load_config(config_path)
        set_config_file(config_path)

    if args.port:
        CONFIG["port"] = args.port
    if args.cookie_file:
        CONFIG["cookie_file"] = args.cookie_file
    if args.proxy:
        CONFIG["proxy"] = args.proxy

    # Initialize account pool
    data_dir = os.path.dirname(config_path) if config_path else os.getcwd()
    account_file = os.path.join(data_dir, "accounts.json")
    set_account_file(account_file)
    init_pool_from_cookie()

    port = CONFIG["port"]
    host = CONFIG.get("host", "0.0.0.0")
    server = ThreadedServer((host, port), GeminiHandler)
    print(f"gemini-web2api v{__version__}")
    print(f"  监听地址:  http://0.0.0.0:{port}")
    print(f"  接口地址:  http://localhost:{port}/v1")
    print(f"  可用模型:  {', '.join(MODELS.keys())}")
    print(f"  Cookie:    {'已配置' if CONFIG.get('cookie_file') else '未配置（匿名模式）'}")
    print(f"  账号池:    {account_file}")
    print(f"  代理:       {CONFIG.get('proxy') or '使用系统环境变量'}")
    print(f"  流式传输:   {'httpx（真流式）' if HAS_HTTPX else 'urllib（缓冲模式）'}")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
        server.shutdown()


if __name__ == "__main__":
    main()
