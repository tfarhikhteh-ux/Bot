from __future__ import annotations

import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Deque, Dict


class SingleInstanceError(RuntimeError):
    """زمانی که نسخه دیگری از ربات در حال اجراست."""


def restrict_file_permissions(path: Path) -> None:
    try:
        if os.name != "nt" and path.exists():
            os.chmod(path, 0o600)
    except OSError:
        pass


class RateLimiter:
    """محدودکننده نرخ درخواست به ازای هر کاربر با پنجره زمانی لغزان."""

    def __init__(self, max_requests: int = 25, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            q = self._buckets.setdefault(key, deque())
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= self.max_requests:
                return False
            q.append(now)
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)


class DuplicateActionGuard:
    """جلوگیری از اجرای تکراری عملیات حساس در بازه کوتاه (دابل‌کلیک یا ارسال تکراری)."""

    def __init__(self, cooldown_seconds: float = 3.0) -> None:
        self.cooldown_seconds = cooldown_seconds
        self._recent: Dict[str, float] = {}
        self._lock = threading.Lock()

    def acquire(self, action_key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            expired = [k for k, exp in self._recent.items() if exp <= now]
            for k in expired:
                del self._recent[k]
            if action_key in self._recent:
                return False
            self._recent[action_key] = now + self.cooldown_seconds
            return True


class SingleInstanceLock:
    """قفل تک‌نمونه‌ای سازگار با Windows و Linux برای جلوگیری از اجرای هم‌زمان ربات."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._fh = None

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.lock_path, "a+b")
        try:
            self._fh.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fh.seek(0)
            self._fh.truncate()
            self._fh.write(f"{os.getpid()}\n".encode("ascii"))
            self._fh.flush()
        except OSError as exc:
            self._fh.close()
            self._fh = None
            raise SingleInstanceError(
                "نسخه دیگری از ربات در حال اجراست. برای جلوگیری از تداخل دریافت پیام‌ها، اجرای جدید متوقف شد."
            ) from exc

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
