"""Admin API and proxy testing."""
import time as _time
from . import gemini
from .config import CONFIG
import urllib.request
import json

def get_current_config() -> dict:
    """获取当前配置（脱敏）。"""
    return {
        "port": CONFIG.get("port"),
        "host": CONFIG.get("host"),
        "proxy": CONFIG.get("proxy"),
        "proxy_pool": CONFIG.get("proxy_pool", []),
        "proxy_strategy": CONFIG.get("proxy_strategy", "round_robin"),
        "default_model": CONFIG.get("default_model"),
        "log_requests": CONFIG.get("log_requests"),
        "proxy_stats": gemini.get_proxy_stats(),
    }

def update_config(new_config: dict) -> bool:
    """更新配置并保存。"""
    allowed_keys = ["proxy", "proxy_pool", "proxy_strategy", "default_model", "log_requests"]
    for k in allowed_keys:
        if k in new_config:
            CONFIG[k] = new_config[k]
    
    # 尝试保存到文件
    from .config import find_config
    cfg_path = find_config() or "./config.json"
    try:
        with open(cfg_path, "w") as f:
            json.dump(CONFIG, f, indent=4)
        return True
    except Exception:
        return False

def get_proxy_stats() -> dict:
    """获取代理统计。"""
    return gemini.get_proxy_stats()

def test_proxy(proxy_url: str) -> dict:
    """测试单个代理的连通性。"""
    test_url = "https://gemini.google.com"
    # 标准化代理 URL
    formatted_proxy = gemini.format_proxy_url(proxy_url)
    
    try:
        start = _time.time()
        if formatted_proxy:
            # 统一使用 httpx 进行测试，支持 SOCKS 和 HTTP
            try:
                import httpx
                with httpx.Client(proxy=formatted_proxy, timeout=10, verify=True, follow_redirects=True) as client:
                    resp = client.get(test_url)
                    resp.raise_for_status()
            except ImportError:
                if formatted_proxy.startswith("socks"):
                    return {"success": False, "error": "SOCKS 代理需要安装 httpx 和 httpx[socks]"}
                # 回退到 urllib
                proxy_handler = urllib.request.ProxyHandler({"http": formatted_proxy, "https": formatted_proxy})
                opener = urllib.request.build_opener(proxy_handler)
                resp = opener.open(test_url, timeout=10)
                resp.read()
            except Exception as e:
                # 记录详细错误
                return {"success": False, "error": f"代理测试失败: {str(e)}"}
        else:
            # 无代理
            resp = urllib.request.urlopen(test_url, timeout=10)
            resp.read()
                
        elapsed = round(_time.time() - start, 2)
        gemini.report_proxy_result(proxy_url, True)
        return {"success": True, "latency": elapsed}
    except Exception as e:
        gemini.report_proxy_result(proxy_url, False)
        return {"success": False, "error": str(e)}

def test_all_proxies() -> dict:
    """测试所有配置的代理。"""
    pool = CONFIG.get("proxy_pool") or []
    single = CONFIG.get("proxy")
    all_proxies = list(pool)
    if single and single not in all_proxies:
        all_proxies.append(single)
    
    results = {}
    for p in all_proxies:
        results[p] = test_proxy(p)
    return results

# ─── Session & Auth Helpers ──────────────────────────────────────────────────
_admin_password_hash = None
_sessions = set()
_config_file = None

def is_password_set() -> bool:
    return _admin_password_hash is not None

def set_password(password: str):
    global _admin_password_hash
    import hashlib
    _admin_password_hash = hashlib.sha256(password.encode()).hexdigest()

def verify_admin_password(password: str) -> bool:
    import hashlib
    h = hashlib.sha256(password.encode()).hexdigest()
    return h == _admin_password_hash

def create_session() -> str:
    import uuid
    token = uuid.uuid4().hex
    _sessions.add(token)
    return token

def verify_session(token: str) -> bool:
    return token in _sessions

def clear_session(token: str):
    if token in _sessions:
        _sessions.remove(token)

def set_config_file(path: str):
    global _config_file
    _config_file = path

def save_cookie(cookie: str):
    CONFIG["cookie_file"] = "cookie.txt"
    with open("cookie.txt", "w") as f:
        f.write(cookie)

def get_cookie() -> str:
    cf = CONFIG.get("cookie_file")
    if cf and os.path.exists(cf):
        with open(cf, "r") as f:
            return f.read()
    return ""
