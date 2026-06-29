import json
import time
import uuid
import re
import urllib.request
import urllib.parse
import ssl
import os
import hashlib
import random
import threading

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from .config import CONFIG

_ssl_ctx = None
_cookie_cache = {"str": "", "sapisid": None, "mtime": 0}
_httpx_client = None
_proxy_idx = 0

# 代理健康检查相关
_proxy_health = {}       # proxy_url -> {"status": "healthy"|"unhealthy", "cooldown_until": float}
_proxy_stats = {}        # proxy_url -> {"requests": int, "success": int, "fail": int, "last_used": float}
_last_request_time = 0.0
_request_lock = threading.Lock()
_proxy_cooldown_sec = 60  # 失败后冷却秒数
min_request_interval = 1.0  # 请求最小间隔（秒）

# 随机 User-Agent 池
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
]


def format_proxy_url(proxy: str | None) -> str | None:
    """标准化代理 URL，支持 ip:port:user:pass 格式。"""
    if not proxy or not isinstance(proxy, str):
        return proxy
    
    proxy = proxy.strip()
    if not proxy:
        return None
        
    # 如果已经是标准协议开头，直接返回
    if any(proxy.startswith(p) for p in ["http://", "https://", "socks5://", "socks4://"]):
        # 特殊处理 socks5://ip:port:user:pass
        parts = proxy.split("://", 1)
        scheme = parts[0]
        rest = parts[1]
        if rest.count(":") == 3:
            # ip:port:user:pass
            host, port, user, pw = rest.split(":")
            return f"{scheme}://{user}:{pw}@{host}:{port}"
        return proxy

    # 处理 ip:port:user:pass (默认为 http)
    if proxy.count(":") == 3:
        host, port, user, pw = proxy.split(":")
        return f"http://{user}:{pw}@{host}:{port}"
    
    # 处理 ip:port (默认为 http)
    if proxy.count(":") == 1:
        return f"http://{proxy}"
        
    return proxy


def get_proxy() -> str | None:
    """从代理池中获取一个代理（兼容旧接口，内部调用 get_healthy_proxy）。"""
    return get_healthy_proxy()


def get_healthy_proxy() -> str | None:
    """从代理池中获取一个健康的代理，跳过冷却中的代理。如果所有代理都在冷却中则重置。"""
    pool = CONFIG.get("proxy_pool") or []
    if not pool:
        return format_proxy_url(CONFIG.get("proxy"))

    now = time.time()
    healthy_proxies = []
    for p in pool:
        health = _proxy_health.get(p)
        if health is None or health["status"] == "healthy":
            healthy_proxies.append(p)
        elif health["status"] == "unhealthy" and now >= health.get("cooldown_until", 0):
            # 冷却已过期，恢复为健康
            _proxy_health[p] = {"status": "healthy", "cooldown_until": 0}
            healthy_proxies.append(p)

    if not healthy_proxies:
        # 所有代理都在冷却中，重置全部为健康
        for p in pool:
            _proxy_health[p] = {"status": "healthy", "cooldown_until": 0}
        healthy_proxies = list(pool)
        log("所有代理都在冷却中，已重置为健康状态")

    strategy = CONFIG.get("proxy_strategy", "round_robin")
    if strategy == "random":
        p = random.choice(healthy_proxies)
    else:
        global _proxy_idx
        p = healthy_proxies[_proxy_idx % len(healthy_proxies)]
        _proxy_idx += 1
    
    return format_proxy_url(p)


def report_proxy_result(proxy: str | None, success: bool):
    """上报代理请求结果，成功时更新统计，失败时标记冷却。"""
    if not proxy:
        return
    with _request_lock:
        now = time.time()
        if proxy not in _proxy_stats:
            _proxy_stats[proxy] = {"requests": 0, "success": 0, "fail": 0, "last_used": 0}
        stats = _proxy_stats[proxy]
        stats["requests"] += 1
        stats["last_used"] = now
        if success:
            stats["success"] += 1
            _proxy_health[proxy] = {"status": "healthy", "cooldown_until": 0}
        else:
            stats["fail"] += 1
            _proxy_health[proxy] = {"status": "unhealthy", "cooldown_until": now + _proxy_cooldown_sec}


def get_proxy_stats() -> dict:
    """返回每个代理的请求次数、成功次数、失败次数及健康状态。"""
    pool = CONFIG.get("proxy_pool") or []
    single = CONFIG.get("proxy")
    all_proxies = list(pool)
    if single and single not in all_proxies:
        all_proxies.append(single)
    result = {}
    now = time.time()
    for p in all_proxies:
        stats = _proxy_stats.get(p, {"requests": 0, "success": 0, "fail": 0, "last_used": 0})
        health = _proxy_health.get(p)
        status = "healthy"
        if health and health["status"] == "unhealthy":
            if now < health.get("cooldown_until", 0):
                status = "unhealthy"
            else:
                status = "healthy"
        result[p] = {
            "requests": stats["requests"],
            "success": stats["success"],
            "fail": stats["fail"],
            "last_used": stats["last_used"],
            "status": status,
        }
    return result


def log(msg: str):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def _get_ssl_ctx():
    global _ssl_ctx
    if _ssl_ctx is None:
        _ssl_ctx = ssl.create_default_context()
        # 如果需要忽略证书错误可以取消下面注释
        # _ssl_ctx.check_hostname = False
        # _ssl_ctx.verify_mode = ssl.CERT_NONE
    return _ssl_ctx


def load_cookie():
    """Load cookie from file or config."""
    global _cookie_cache
    now = time.time()
    if _cookie_cache["str"] and now - _cookie_cache["mtime"] < 60:
        return _cookie_cache["str"], _cookie_cache["sapisid"]

    cookie_file = CONFIG.get("cookie_file")
    cookie_str = ""
    if cookie_file and os.path.exists(cookie_file):
        try:
            with open(cookie_file, "r") as f:
                cookie_str = f.read().strip()
        except Exception as e:
            log(f"读取 Cookie 文件失败: {e}")
    
    if not cookie_str:
        # 尝试从环境变量或配置中获取
        cookie_str = os.environ.get("GEMINI_COOKIE", "")
    
    sapisid = None
    if cookie_str:
        m = re.search(r"SAPISID=([^;]+)", cookie_str)
        if m:
            sapisid = m.group(1)
    
    _cookie_cache = {"str": cookie_str, "sapisid": sapisid, "mtime": now}
    return cookie_str, sapisid


def make_sapisidhash(sapisid: str) -> str:
    """Generate SAPISIDHASH for Authorization header."""
    now = int(time.time())
    origin = "https://gemini.google.com"
    payload = f"{now} {sapisid} {origin}"
    hash_val = hashlib.sha1(payload.encode()).hexdigest()
    return f"SAPISIDHASH {now}_{hash_val}"


def _get_url() -> str:
    bl = CONFIG.get("gemini_bl", "boq_assistant-bard-web-server_20260525.09_p0")
    return f"https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardChatUi/BatchInvite?rpcids=at9mu&bl={bl}&_reqid={random.randint(100000, 999999)}&rt=c"


def _build_headers():
    cookie_str, sapisid = load_cookie()
    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        "X-Same-Domain": "1",
    }
    if cookie_str:
        headers["Cookie"] = cookie_str
    if sapisid:
        headers["Authorization"] = make_sapisidhash(sapisid)
    
    # 账号池支持（如果有配置的话）
    acc_id = "default"
    return headers, acc_id


def _build_payload(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None) -> str:
    # 简化版 Payload 构建逻辑，实际可能更复杂
    # model_id: 1=flash, 2=pro, 3=ultra
    # think_mode: 0=normal, 1=thinking
    at_token = "" # 实际需要从页面获取
    
    # 这里通常是一个复杂的嵌套列表结构，经过 URL 编码
    req_data = [
        None,
        json.dumps([[prompt, 0, None, file_refs or []], None, [str(model_id)]])
    ]
    payload = {
        "f.req": json.dumps([None, json.dumps(req_data)]),
        "at": at_token,
    }
    return urllib.parse.urlencode(payload)


def extract_response_text(raw: str) -> str:
    # 简化版响应解析
    try:
        # Gemini 的响应通常是多行 JSON 块
        lines = raw.split("\n")
        for line in lines:
            if "at9mu" in line:
                data = json.loads(line)
                # 深入解析嵌套结构获取文本内容...
                return "Gemini Response Text" # 占位
    except:
        pass
    return raw


def _check_bard_error(buf: str):
    if "BardErrorInfo" in buf:
        raise RuntimeError("Gemini 业务错误: " + buf[:200])


def _extract_texts_from_line(line: str):
    # 流式文本提取逻辑
    return []


def clean_text(text: str) -> str:
    return text.strip()


def generate(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None):
    """Simple generation via httpx or urllib with retry."""
    body = _build_payload(prompt, model_id, think_mode, file_refs, extra_fields).encode()
    url = _get_url()
    headers, acc_id = _build_headers()
    ctx = _get_ssl_ctx()

    last_err = None
    for attempt in range(CONFIG["retry_attempts"]):
        proxy = None
        try:
            proxy = get_healthy_proxy()
            if HAS_HTTPX:
                transport = httpx.HTTPTransport(proxy=proxy) if proxy else None
                with httpx.Client(transport=transport, timeout=CONFIG["request_timeout_sec"], verify=True) as client:
                    resp = client.post(url, content=body, headers=headers)
                    raw = resp.text
            else:
                req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                if proxy:
                    opener = urllib.request.build_opener(
                        urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
                        urllib.request.HTTPSHandler(context=ctx)
                    )
                    resp = opener.open(req, timeout=CONFIG["request_timeout_sec"])
                else:
                    resp = urllib.request.urlopen(req, context=ctx, timeout=CONFIG["request_timeout_sec"])
                raw = resp.read().decode("utf-8", errors="replace")
            report_proxy_result(proxy, True)
            return extract_response_text(raw), acc_id
        except Exception as e:
            last_err = e
            report_proxy_result(proxy, False)
            if attempt < CONFIG["retry_attempts"] - 1:
                log(f"重试 {attempt+1}/{CONFIG['retry_attempts']}: {e}")
                time.sleep(CONFIG["retry_delay_sec"])
    raise last_err


def generate_stream(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None):
    """Streaming generation via httpx with retry on connection failure."""
    if not HAS_HTTPX:
        text, acc_id = generate(prompt, model_id, think_mode, file_refs, extra_fields)
        if text:
            yield text, acc_id
        return

    global _last_request_time
    # 请求间隔控制
    with _request_lock:
        elapsed = time.time() - _last_request_time
        if elapsed < min_request_interval:
            time.sleep(min_request_interval - elapsed)
        _last_request_time = time.time()

    body = _build_payload(prompt, model_id, think_mode, file_refs, extra_fields)
    url = _get_url()
    headers, acc_id = _build_headers()

    last_err = None
    for attempt in range(CONFIG["retry_attempts"]):
        proxy = None
        try:
            proxy = get_healthy_proxy()
            transport = httpx.HTTPTransport(proxy=proxy) if proxy else None
            with httpx.Client(transport=transport, timeout=CONFIG["request_timeout_sec"], verify=True) as client:
                prev_text = ""
                with client.stream("POST", url, content=body, headers=headers) as resp:
                    buf = ""
                    for chunk in resp.iter_text():
                        buf += chunk
                        if "BardErrorInfo" in buf:
                            _check_bard_error(buf)
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            for t in _extract_texts_from_line(line):
                                if len(t) > len(prev_text):
                                    delta = clean_text(t[len(prev_text):])
                                    if delta:
                                        yield delta, acc_id
                                    prev_text = t
            report_proxy_result(proxy, True)
            return
        except Exception as e:
            last_err = e
            report_proxy_result(proxy, False)
            if attempt < CONFIG["retry_attempts"] - 1:
                log(f"流式重试 {attempt+1}/{CONFIG['retry_attempts']}: {e}")
                time.sleep(CONFIG["retry_delay_sec"])
    raise last_err
