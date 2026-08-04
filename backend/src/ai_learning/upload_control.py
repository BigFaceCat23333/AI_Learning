"""文档上传取消状态管理。

当前服务使用单进程内存状态协调同步上传请求与取消请求；取消标记保留一小时，
既能处理文件仍在传输时先到达的取消请求，也会自动清理过期状态。
"""

import threading
import time

_CANCEL_TTL_SECONDS = 60 * 60
_lock = threading.Lock()
_entries: dict[tuple[int, str], tuple[threading.Event, float]] = {}


def _cleanup_expired(now: float) -> None:
    expired = [
        key
        for key, (_event, updated_at) in _entries.items()
        if now - updated_at > _CANCEL_TTL_SECONDS
    ]
    for key in expired:
        _entries.pop(key, None)


def register_upload(user_id: int, upload_id: str) -> threading.Event:
    """注册上传并返回其取消事件；保留可能提前到达的取消标记。"""
    key = (user_id, upload_id)
    now = time.monotonic()
    with _lock:
        _cleanup_expired(now)
        existing = _entries.get(key)
        event = existing[0] if existing is not None else threading.Event()
        _entries[key] = (event, now)
        return event


def request_upload_cancel(user_id: int, upload_id: str) -> None:
    """幂等记录取消请求，同一用户只能影响自己的上传标识。"""
    key = (user_id, upload_id)
    now = time.monotonic()
    with _lock:
        _cleanup_expired(now)
        existing = _entries.get(key)
        event = existing[0] if existing is not None else threading.Event()
        event.set()
        _entries[key] = (event, now)


def finish_upload(user_id: int, upload_id: str) -> None:
    """上传请求结束后清理状态。"""
    with _lock:
        _entries.pop((user_id, upload_id), None)
