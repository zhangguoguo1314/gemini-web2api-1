"""Admin UI backend: password auth and config management."""
import json
import os
import secrets
import hashlib
from .config import CONFIG

# Admin password storage
_ADMIN_PASSWORD_FILE = os.path.join(os.path.dirname(__file__), ".admin_pass")
_SESSIONS = {}  # session_token -> expiry


def _hash_password(password: str) -> str:
    """Hash password with salt."""
    salt = secrets.token_hex(16)
    h = hashlib.sha256((password + salt).encode()).hexdigest()
    return f"{salt}${h}"


def _verify_password(password: str, stored: str) -> bool:
    """Verify password against stored hash."""
    if not stored or "$" not in stored:
        return False
    salt, h = stored.split("$", 1)
    return hashlib.sha256((password + salt).encode()).hexdigest() == h


def is_password_set() -> bool:
    """Check if admin password has been set."""
    return os.path.exists(_ADMIN_PASSWORD_FILE)


def set_password(password: str):
    """Set admin password."""
    with open(_ADMIN_PASSWORD_FILE, "w") as f:
        f.write(_hash_password(password))


def verify_admin_password(password: str) -> bool:
    """Verify admin password."""
    if not os.path.exists(_ADMIN_PASSWORD_FILE):
        return False
    with open(_ADMIN_PASSWORD_FILE) as f:
        stored = f.read().strip()
    return _verify_password(password, stored)


def create_session() -> str:
    """Create a new session token."""
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = True
    return token


def verify_session(token: str) -> bool:
    """Verify session token."""
    return token in _SESSIONS


def clear_session(token: str):
    """Clear a session."""
    _SESSIONS.pop(token, None)


# Config management
_CONFIG_FILE = None


def set_config_file(path: str):
    """Set the config file path for saving."""
    global _CONFIG_FILE
    _CONFIG_FILE = path


def get_config_file() -> str:
    """Get current config file path."""
    return _CONFIG_FILE


def save_config_to_file(config: dict):
    """Save config to file."""
    path = _CONFIG_FILE or "./config.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def get_current_config() -> dict:
    """Get current runtime config (excluding sensitive fields)."""
    from . import gemini
    return {
        "port": CONFIG.get("port", 8081),
        "host": CONFIG.get("host", "0.0.0.0"),
        "default_model": CONFIG.get("default_model", "gemini-3.5-flash"),
        "api_keys": CONFIG.get("api_keys", []),
        "proxy": CONFIG.get("proxy"),
        "proxy_pool": CONFIG.get("proxy_pool", []),
        "proxy_strategy": CONFIG.get("proxy_strategy", "round_robin"),
        "cookie_file": CONFIG.get("cookie_file"),
        "retry_attempts": CONFIG.get("retry_attempts", 3),
        "retry_delay_sec": CONFIG.get("retry_delay_sec", 2),
        "request_timeout_sec": CONFIG.get("request_timeout_sec", 180),
        "log_requests": CONFIG.get("log_requests", True),
        "proxy_stats": gemini.get_proxy_stats(),
    }


def update_config(updates: dict):
    """Update runtime config and save to file."""
    for key, value in updates.items():
        if key in CONFIG:
            CONFIG[key] = value
    save_config_to_file(dict(CONFIG))


def save_cookie(cookie: str):
    """Save cookie to a file and update config."""
    cookie_path = os.path.join(os.path.dirname(_ADMIN_PASSWORD_FILE), ".cookie.txt")
    with open(cookie_path, "w", encoding="utf-8") as f:
        f.write(cookie)
    CONFIG["cookie_file"] = cookie_path
    save_config_to_file(dict(CONFIG))
    return cookie_path


def get_cookie() -> str:
    """Get current cookie content."""
    cookie_file = CONFIG.get("cookie_file")
    if cookie_file and os.path.exists(cookie_file):
        with open(cookie_file, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def get_proxy_stats() -> dict:
    """获取代理池统计信息。"""
    from . import gemini
    return gemini.get_proxy_stats()


def test_proxy(proxy_url: str) -> dict:
    """测试单个代理的连通性。"""
    import time as _time
    from . import gemini
    test_url = "https://gemini.google.com"
    try:
        start = _time.time()
        if proxy_url.startswith("socks"):
            # SOCKS 代理需要 httpx 或 PySocks
            try:
                import httpx
                transport = httpx.HTTPTransport(proxy=proxy_url)
                with httpx.Client(transport=transport, timeout=10, verify=True) as client:
                    resp = client.get(test_url)
                    resp.raise_for_status()
            except ImportError:
                return {"success": False, "error": "SOCKS 代理需要安装 httpx 和 httpx[socks]"}
        else:
            import urllib.request
            proxy_handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            opener = urllib.request.build_opener(proxy_handler)
            resp = opener.open(test_url, timeout=10)
            resp.read()
        elapsed = round(_time.time() - start, 2)
        gemini.report_proxy_result(proxy_url, True)
        return {"success": True, "latency": elapsed}
    except Exception as e:
        gemini.report_proxy_result(proxy_url, False)
        return {"success": False, "error": str(e)}


def test_all_proxies() -> dict:
    """批量测试所有代理的连通性。"""
    pool = CONFIG.get("proxy_pool") or []
    single = CONFIG.get("proxy")
    all_proxies = list(pool)
    if single and single not in all_proxies:
        all_proxies.append(single)
    results = {}
    for p in all_proxies:
        results[p] = test_proxy(p)
    return results
