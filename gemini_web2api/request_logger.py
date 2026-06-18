"""Request logging and token statistics."""
import json
import os
import time
import threading
from collections import deque

# In-memory log storage (keep last 1000 entries)
_MAX_LOGS = 1000
_logs = deque(maxlen=_MAX_LOGS)
_log_lock = threading.RLock()

# Daily stats
_daily_stats = {
    "date": time.strftime("%Y-%m-%d"),
    "requests": 0,
    "tokens": 0,
    "errors": 0,
}
_stats_lock = threading.RLock()


def log_request(method: str, path: str, model: str = None, status: str = "success",
                tokens: int = 0, duration_ms: int = 0, account_id: str = None,
                error_msg: str = None, client_ip: str = None):
    """Log a request."""
    entry = {
        "id": f"req_{int(time.time() * 1000)}_{os.urandom(4).hex()}",
        "timestamp": int(time.time()),
        "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        "method": method,
        "path": path,
        "model": model,
        "status": status,
        "tokens": tokens,
        "duration_ms": duration_ms,
        "account_id": account_id,
        "error_msg": error_msg,
        "client_ip": client_ip,
    }
    with _log_lock:
        _logs.append(entry)

    # Update daily stats
    with _stats_lock:
        global _daily_stats
        today = time.strftime("%Y-%m-%d")
        if _daily_stats["date"] != today:
            _daily_stats = {"date": today, "requests": 0, "tokens": 0, "errors": 0}
        _daily_stats["requests"] += 1
        _daily_stats["tokens"] += tokens
        if status == "error":
            _daily_stats["errors"] += 1


def get_logs(limit: int = 100, offset: int = 0, status: str = None, model: str = None) -> dict:
    """Get paginated logs with optional filters."""
    with _log_lock:
        filtered = list(_logs)
    if status:
        filtered = [l for l in filtered if l["status"] == status]
    if model:
        filtered = [l for l in filtered if l.get("model") == model]
    # Sort by timestamp desc
    filtered.sort(key=lambda x: x["timestamp"], reverse=True)
    total = len(filtered)
    sliced = filtered[offset:offset + limit]
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
        "logs": sliced,
    }


def get_stats() -> dict:
    """Get overall statistics."""
    with _stats_lock:
        daily = dict(_daily_stats)
    with _log_lock:
        total_requests = len(_logs)
        total_tokens = sum(l.get("tokens", 0) for l in _logs)
        total_errors = sum(1 for l in _logs if l["status"] == "error")
        # Model breakdown with tokens
        model_stats = {}
        for l in _logs:
            m = l.get("model") or "unknown"
            if m not in model_stats:
                model_stats[m] = {"requests": 0, "tokens": 0}
            model_stats[m]["requests"] += 1
            model_stats[m]["tokens"] += l.get("tokens", 0)
    return {
        "today": daily,
        "total_requests": total_requests,
        "total_tokens": total_tokens,
        "total_errors": total_errors,
        "models": model_stats,
    }


def get_log_detail(log_id: str) -> dict:
    """Get a single log entry by ID."""
    with _log_lock:
        for l in _logs:
            if l["id"] == log_id:
                return l
    return None
