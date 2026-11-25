from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from database.db import DatabaseManager
from utils.dates import compute_week_type, now_iso


class SettingsRepository:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_row(self, key: str) -> Optional[Dict[str, Any]]:
        return self.db.fetchone("SELECT * FROM settings WHERE key = ?;", (key,))

    def get(self, key: str, default: str = "") -> str:
        row = self.get_row(key)
        return row["value"] if row is not None else default

    def get_bool(self, key: str, default: bool = False) -> bool:
        raw = self.get(key, "1" if default else "0").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def get_int(self, key: str, default: int = 0) -> int:
        raw = self.get(key, str(default)).strip()
        try:
            return int(raw)
        except ValueError:
            return default

    def get_teaching_days(self) -> List[int]:
        raw = self.get("teaching_days", "[0, 1, 2, 3, 4]")
        try:
            days = json.loads(raw)
            return sorted({int(d) for d in days if 0 <= int(d) <= 6})
        except Exception:
            return [0, 1, 2, 3, 4]

    def get_active_week_type(self, target_iso_date: Optional[str] = None) -> str:
        mode = self.get("active_week_mode", "AUTO").upper()
        manual = mode if mode in {"WEEK_1", "WEEK_2"} else None
        term1_start = self.get("term1_start", "")
        return compute_week_type(target_iso_date=target_iso_date, term_start_iso=term1_start, manual_override=manual)

    def set_value(
        self,
        key: str,
        value: str,
        updated_by: Optional[int] = None,
        category: str = "GENERAL",
        admin_only: int = 0,
    ) -> None:
        ts = now_iso()
        existing = self.get_row(key)
        if existing is None:
            self.db.execute(
                """
                INSERT INTO settings(key, value, category, admin_only, updated_by, updated_at)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (key, str(value), category, int(bool(admin_only)), updated_by, ts),
            )
        else:
            self.db.execute(
                "UPDATE settings SET value = ?, updated_by = ?, updated_at = ? WHERE key = ?;",
                (str(value), updated_by, ts, key),
            )

    def list_all(self, include_admin_only: bool = True) -> List[Dict[str, Any]]:
        if include_admin_only:
            return self.db.fetchall("SELECT * FROM settings ORDER BY category, key;")
        return self.db.fetchall("SELECT * FROM settings WHERE admin_only = 0 ORDER BY category, key;")

    def claim_task_slot(self, task_key: str, task_type: str, slot: str, details: str = "") -> bool:
        """ثبت اتمیک نوبت اجرای وظایف زمان‌بندی‌شده برای جلوگیری از اجرای تکراری پس از ری‌استارت."""
        ts = now_iso()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT last_run_slot FROM scheduled_tasks WHERE task_key = ?;", (task_key,)).fetchone()
            if row and row["last_run_slot"] == slot:
                return False
            conn.execute(
                """
                INSERT INTO scheduled_tasks(task_key, task_type, last_run_slot, last_run_at, status, details)
                VALUES (?, ?, ?, ?, 'OK', ?)
                ON CONFLICT(task_key) DO UPDATE SET
                    last_run_slot = excluded.last_run_slot,
                    last_run_at = excluded.last_run_at,
                    status = 'OK',
                    details = excluded.details;
                """,
                (task_key, task_type, slot, ts, details),
            )
            return True
