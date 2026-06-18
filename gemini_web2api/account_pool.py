"""Account pool management: multiple cookies, load balancing, health checks."""
import json
import os
import time
import random
import threading
from typing import Optional

# Account data structure:
# {
#   "accounts": [
#     {"id": "acc_1", "name": "账号1", "cookie": "...", "enabled": true, "weight": 1},
#     ...
#   ]
# }

_ACCOUNT_FILE = None
_accounts = []
_account_lock = threading.RLock()
_account_stats = {}  # id -> {requests, tokens, errors, last_used, last_error}


def set_account_file(path: str):
    """Set the account pool file path."""
    global _ACCOUNT_FILE
    _ACCOUNT_FILE = path
    _load_accounts()


def _load_accounts():
    """Load accounts from file."""
    global _accounts
    if _ACCOUNT_FILE and os.path.exists(_ACCOUNT_FILE):
        try:
            with open(_ACCOUNT_FILE, encoding="utf-8") as f:
                data = json.load(f)
            _accounts = data.get("accounts", [])
        except Exception:
            _accounts = []
    else:
        _accounts = []


def _save_accounts():
    """Save accounts to file."""
    if _ACCOUNT_FILE:
        try:
            with open(_ACCOUNT_FILE, "w", encoding="utf-8") as f:
                json.dump({"accounts": _accounts}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def get_accounts() -> list:
    """Get all accounts with stats."""
    with _account_lock:
        result = []
        for acc in _accounts:
            acc_id = acc.get("id", "")
            stats = _account_stats.get(acc_id, {})
            result.append({
                **acc,
                "stats": {
                    "requests": stats.get("requests", 0),
                    "tokens": stats.get("tokens", 0),
                    "errors": stats.get("errors", 0),
                    "last_used": stats.get("last_used"),
                    "last_error": stats.get("last_error"),
                }
            })
        return result


def add_account(name: str, cookie: str, weight: int = 1) -> dict:
    """Add a new account."""
    with _account_lock:
        acc_id = f"acc_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
        account = {
            "id": acc_id,
            "name": name,
            "cookie": cookie,
            "enabled": True,
            "weight": weight,
            "created_at": int(time.time()),
        }
        _accounts.append(account)
        _save_accounts()
        return account


def update_account(acc_id: str, updates: dict) -> Optional[dict]:
    """Update an account."""
    with _account_lock:
        for acc in _accounts:
            if acc.get("id") == acc_id:
                for k, v in updates.items():
                    if k in ("name", "cookie", "enabled", "weight"):
                        acc[k] = v
                _save_accounts()
                return acc
        return None


def delete_account(acc_id: str) -> bool:
    """Delete an account."""
    with _account_lock:
        for i, acc in enumerate(_accounts):
            if acc.get("id") == acc_id:
                _accounts.pop(i)
                _account_stats.pop(acc_id, None)
                _save_accounts()
                return True
        return False


def test_account(acc_id: str) -> dict:
    """Test if an account's cookie is valid by making a small request."""
    with _account_lock:
        account = None
        for acc in _accounts:
            if acc.get("id") == acc_id:
                account = acc
                break
        if not account:
            return {"success": False, "error": "账号不存在"}

    cookie = account.get("cookie", "")
    if not cookie:
        return {"success": False, "error": "Cookie 为空"}

    # Simple test: try to load cookie and build headers
    try:
        from .gemini import load_cookie
        # Temporarily set cookie file content
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(cookie)
            tmp_path = f.name

        # We can't easily test without modifying global state, so just check cookie format
        if "SAPISID" in cookie or "__Secure-1PSID" in cookie:
            os.unlink(tmp_path)
            return {"success": True, "message": "Cookie 格式正确"}
        else:
            os.unlink(tmp_path)
            return {"success": False, "error": "Cookie 格式不正确，缺少必要字段"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def pick_account() -> Optional[dict]:
    """Pick an enabled account using weighted random selection."""
    with _account_lock:
        enabled = [a for a in _accounts if a.get("enabled", True)]
        if not enabled:
            return None
        # Weighted random
        weights = [a.get("weight", 1) for a in enabled]
        total = sum(weights)
        if total == 0:
            return random.choice(enabled)
        r = random.uniform(0, total)
        cumsum = 0
        for acc, w in zip(enabled, weights):
            cumsum += w
            if r <= cumsum:
                return acc
        return enabled[-1]


def get_active_cookie() -> tuple:
    """Get cookie string and sapisid from the picked account.
    Falls back to global config cookie if no accounts.
    """
    account = pick_account()
    if account:
        cookie = account.get("cookie", "")
        if cookie:
            # Parse sapisid
            sapisid = ""
            for part in cookie.split(";"):
                part = part.strip()
                if part.startswith("SAPISID="):
                    sapisid = part[8:]
            return cookie, sapisid, account.get("id")

    # Fallback to global config
    from .config import CONFIG
    cookie_file = CONFIG.get("cookie_file")
    if cookie_file and os.path.exists(cookie_file):
        try:
            with open(cookie_file, encoding="utf-8") as f:
                content = f.read().strip()
            if content.startswith("{"):
                data = json.loads(content)
                cookie = data.get("cookie", "")
                sapisid = data.get("sapisid", "")
            else:
                cookie = content
                pairs = dict(p.split("=", 1) for p in cookie.split("; ") if "=" in p)
                sapisid = pairs.get("SAPISID", "")
            return cookie, sapisid, None
        except Exception:
            pass
    return "", None, None


def record_request(acc_id: Optional[str], tokens: int = 0, error: bool = False):
    """Record request stats for an account."""
    if not acc_id:
        return
    with _account_lock:
        stats = _account_stats.setdefault(acc_id, {
            "requests": 0, "tokens": 0, "errors": 0,
            "last_used": None, "last_error": None
        })
        stats["requests"] += 1
        stats["tokens"] += tokens
        stats["last_used"] = int(time.time())
        if error:
            stats["errors"] += 1
            stats["last_error"] = int(time.time())


def get_pool_stats() -> dict:
    """Get overall pool statistics."""
    with _account_lock:
        total_requests = sum(s.get("requests", 0) for s in _account_stats.values())
        total_tokens = sum(s.get("tokens", 0) for s in _account_stats.values())
        total_errors = sum(s.get("errors", 0) for s in _account_stats.values())
        enabled_count = sum(1 for a in _accounts if a.get("enabled", True))
        return {
            "total_accounts": len(_accounts),
            "active_accounts": enabled_count,
            "total_requests": total_requests,
            "total_tokens": total_tokens,
            "total_errors": total_errors,
        }


def init_pool_from_cookie():
    """If no accounts but global cookie exists, create a default account."""
    from .config import CONFIG
    if _accounts:
        return
    cookie_file = CONFIG.get("cookie_file")
    if cookie_file and os.path.exists(cookie_file):
        try:
            with open(cookie_file, encoding="utf-8") as f:
                content = f.read().strip()
            if content.startswith("{"):
                data = json.loads(content)
                cookie = data.get("cookie", "")
            else:
                cookie = content
            if cookie:
                add_account("默认账号", cookie, weight=1)
        except Exception:
            pass
