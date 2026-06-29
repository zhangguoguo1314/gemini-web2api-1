"""Gemini StreamGenerate protocol implementation with httpx streaming."""
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


def get_proxy() -> str | None:
    """从代理池中获取一个代理（兼容旧接口，内部调用 get_healthy_proxy）。"""
    return get_healthy_proxy()


def get_healthy_proxy() -> str | None:
    """从代理池中获取一个健康的代理，跳过冷却中的代理。如果所有代理都在冷却中则重置。"""
    pool = CONFIG.get("proxy_pool") or []
    if not pool:
        return CONFIG.get("proxy")

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
        return random.choice(healthy_proxies)

    global _proxy_idx
    # 从健康代理中按轮询选择
    proxy = healthy_proxies[_proxy_idx % len(healthy_proxies)]
    _proxy_idx += 1
    return proxy


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
    if CONFIG["log_requests"]:
        import sys
        sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        sys.stderr.flush()


def _get_ssl_ctx():
    global _ssl_ctx
    if _ssl_ctx is None:
        _ssl_ctx = ssl.create_default_context()
    return _ssl_ctx


def load_cookie() -> tuple:
    """Load cookie from file with mtime-based caching, or from account pool."""
    # Try account pool first
    try:
        from .account_pool import get_active_cookie
        cookie_str, sapisid, acc_id = get_active_cookie()
        if cookie_str:
            return cookie_str, sapisid, acc_id
    except Exception:
        pass

    # Fallback to global config
    cookie_file = CONFIG.get("cookie_file")
    if not cookie_file or not os.path.exists(cookie_file):
        return "", None, None
    try:
        mtime = os.path.getmtime(cookie_file)
        if mtime == _cookie_cache["mtime"] and _cookie_cache["str"]:
            return _cookie_cache["str"], _cookie_cache["sapisid"], None
        with open(cookie_file, "r") as f:
            content = f.read().strip()
        if content.startswith("{"):
            data = json.loads(content)
            cookie_str = data.get("cookie", "")
            sapisid = data.get("sapisid", "")
        else:
            cookie_str = content
            pairs = dict(p.split("=", 1) for p in cookie_str.split("; ") if "=" in p)
            sapisid = pairs.get("SAPISID", "")
        _cookie_cache.update({"str": cookie_str, "sapisid": sapisid or None, "mtime": mtime})
        return _cookie_cache["str"], _cookie_cache["sapisid"], None
    except Exception as e:
        log(f"Cookie 加载失败: {e}")
        return _cookie_cache["str"], _cookie_cache["sapisid"], None


def make_sapisidhash(sapisid: str) -> str:
    ts = int(time.time())
    h = hashlib.sha1(f"{ts} {sapisid} https://gemini.google.com".encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{h}"


def _account_prefix() -> str:
    """Return the Gemini account path prefix for non-default Google accounts."""
    auth_user = CONFIG.get("auth_user")
    if auth_user is None or auth_user == "":
        return ""
    return f"/u/{auth_user}"


def _build_headers() -> dict:
    account_prefix = _account_prefix()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://gemini.google.com",
        "Referer": f"https://gemini.google.com{account_prefix}/app",
        "X-Same-Domain": "1",
        "User-Agent": random.choice(_USER_AGENTS),
    }
    if account_prefix:
        headers["X-Goog-AuthUser"] = str(CONFIG["auth_user"])
    cookie_str, sapisid, acc_id = load_cookie()
    if cookie_str:
        headers["Cookie"] = cookie_str
    if sapisid:
        headers["Authorization"] = make_sapisidhash(sapisid)
    return headers, acc_id


def _build_payload(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None) -> str:
    inner = [None] * 102
    if file_refs:
        refs = [[None, None, ref] for ref in file_refs]
        inner[0] = [prompt, 0, None, refs, None, None, 0]
    else:
        inner[0] = [prompt, 0, None, None, None, None, 0]
    inner[1] = ["en"]
    inner[2] = ["", "", "", None, None, None, None, None, None, ""]
    inner[6] = [0]
    inner[7] = 1
    inner[10] = 1
    inner[11] = 0
    inner[17] = [[think_mode]]
    inner[18] = 0
    inner[27] = 1
    inner[30] = [4]
    inner[41] = [2]
    inner[53] = 0
    inner[59] = str(uuid.uuid4())
    inner[61] = []
    inner[68] = 1
    inner[79] = model_id
    if extra_fields:
        for k, v in extra_fields.items():
            inner[k] = v
    outer = [None, json.dumps(inner)]
    params = {"f.req": json.dumps(outer)}
    if CONFIG.get("xsrf_token"):
        params["at"] = CONFIG["xsrf_token"]
    return urllib.parse.urlencode(params)


def _get_url() -> str:
    reqid = int(time.time()) % 1000000
    account_prefix = _account_prefix()
    return (
        f"https://gemini.google.com{account_prefix}/_/BardChatUi/data/"
        "assistant.lamda.BardFrontendService/StreamGenerate"
        f"?bl={CONFIG['gemini_bl']}&hl=en&_reqid={reqid}&rt=c"
    )


def clean_text(text: str) -> str:
    text = re.sub(
        r'```(?:python|javascript|text)\?code_(?:reference|stdout)&code_event_index=\d+\n.*?```\n?',
        '', text, flags=re.DOTALL
    )
    text = re.sub(r'http://googleusercontent\.com/card_content/\d+\n?', '', text)
    return text.strip()


def _extract_texts_from_line(line: str) -> list:
    """Parse a single wrb.fr line and return list of text strings found."""
    if '"wrb.fr"' not in line or len(line) < 200:
        return []
    try:
        arr = json.loads(line)
        inner_str = arr[0][2]
        if not inner_str or len(inner_str) < 50:
            return []
        inner = json.loads(inner_str)
        if not (isinstance(inner, list) and len(inner) > 4 and inner[4]):
            return []
        texts = []
        for part in inner[4]:
            if isinstance(part, list) and len(part) > 1 and part[1] and isinstance(part[1], list):
                for t in part[1]:
                    if isinstance(t, str) and t:
                        texts.append(t)
        return texts
    except (json.JSONDecodeError, IndexError, TypeError):
        return []


def _check_bard_error(raw: str):
    """Check if Gemini returned BardErrorInfo and raise clear error."""
    bard_err = re.search(r'BardErrorInfo\s*\[(\d+)\]', raw)
    if bard_err:
        code = bard_err.group(1)
        msg = f"Gemini 上游拒绝请求: BardErrorInfo [{code}]"
        if code == "1060":
            msg += " - 当前 IP 被 Google 风控，请配置 Cookie 或更换代理"
        elif code == "1002":
            msg += " - Cookie 无效或已过期，请重新获取"
        raise RuntimeError(msg)


def extract_response_text(raw: str) -> str:
    """Parse full response to get final text."""
    _check_bard_error(raw)
    last_text = ""
    for line in raw.split("\n"):
        for t in _extract_texts_from_line(line):
            if len(t) > len(last_text):
                last_text = t
    return clean_text(last_text)


def generate(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None) -> tuple:
    """Non-streaming generation with retry. Returns (text, account_id)."""
    # 请求间隔控制
    with _request_lock:
        elapsed = time.time() - _last_request_time
        if elapsed < min_request_interval:
            time.sleep(min_request_interval - elapsed)
        _last_request_time = time.time()

    body = _build_payload(prompt, model_id, think_mode, file_refs, extra_fields).encode()
    url = _get_url()
    headers, acc_id = _build_headers()
    ctx = _get_ssl_ctx()

    last_err = None
    for attempt in range(CONFIG["retry_attempts"]):
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
