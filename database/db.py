from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

from utils.security import restrict_file_permissions


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


class DatabaseManager:
    """مدیریت متمرکز، امن و نخ‌ایمن اتصال‌ها و تراکنش‌های SQLite با پشتیبانی WAL و Foreign Key."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        self._local = threading.local()

    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=15.0,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 10000;")
        restrict_file_permissions(self.db_path)
        return conn

    def get_connection(self) -> sqlite3.Connection:
        conn: Optional[sqlite3.Connection] = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._create_connection()
            self._local.conn = conn
        return conn

    def close_thread_connection(self) -> None:
        conn: Optional[sqlite3.Connection] = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
            self._local.conn = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """اجرای اتمیک تراکنش با قفل نوشتن ایمن، Commit خودکار یا Rollback در صورت خطا."""
        with self._write_lock:
            conn = self.get_connection()
            in_outer_tx = conn.in_transaction
            if not in_outer_tx:
                conn.execute("BEGIN IMMEDIATE;")
            try:
                yield conn
                if not in_outer_tx:
                    conn.execute("COMMIT;")
            except Exception:
                if not in_outer_tx and conn.in_transaction:
                    conn.execute("ROLLBACK;")
                raise

    def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        cur = conn.execute(sql, tuple(params))
        return _row_to_dict(cur.fetchone())

    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        cur = conn.execute(sql, tuple(params))
        return [dict(r) for r in cur.fetchall()]

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.transaction() as conn:
            cur = conn.execute(sql, tuple(params))
            return int(cur.lastrowid or cur.rowcount or 0)

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence[Any]]) -> int:
        with self.transaction() as conn:
            cur = conn.executemany(sql, [tuple(p) for p in seq_of_params])
            return int(cur.rowcount or 0)

    def executescript(self, script: str) -> None:
        with self._write_lock:
            conn = self.get_connection()
            conn.executescript(script)
            conn.execute("PRAGMA foreign_keys = ON;")

    def check_integrity(self) -> bool:
        row = self.fetchone("PRAGMA integrity_check;")
        return bool(row and str(list(row.values())[0]).lower() == "ok")
