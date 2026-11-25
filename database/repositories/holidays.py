from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import DatabaseManager
from utils.dates import now_iso


class HolidayRepository:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_by_id(self, holiday_id: int) -> Optional[Dict[str, Any]]:
        return self.db.fetchone("SELECT * FROM holidays WHERE id = ?;", (holiday_id,))

    def create(
        self,
        holiday_date: str,
        title: str,
        holiday_type: str = "OFFICIAL",
        end_date: Optional[str] = None,
        is_full_close: int = 1,
        description: Optional[str] = None,
        created_by: Optional[int] = None,
    ) -> Dict[str, Any]:
        e_date = end_date or holiday_date
        if e_date < holiday_date:
            raise ValueError("تاریخ پایان تعطیلات نباید پیش از تاریخ شروع باشد.")
        hid = self.db.execute(
            """
            INSERT INTO holidays(
                holiday_date, end_date, title, holiday_type,
                is_full_close, description, created_by, notified, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?);
            """,
            (
                holiday_date,
                e_date,
                title.strip(),
                holiday_type,
                int(bool(is_full_close)),
                description,
                created_by,
                now_iso(),
            ),
        )
        return self.get_by_id(hid)

    def delete(self, holiday_id: int) -> bool:
        cnt = self.db.execute("DELETE FROM holidays WHERE id = ?;", (holiday_id,))
        return cnt > 0

    def mark_notified(self, holiday_id: int) -> None:
        self.db.execute("UPDATE holidays SET notified = 1 WHERE id = ?;", (holiday_id,))

    def find_closed_holiday_on(self, target_date: str) -> Optional[Dict[str, Any]]:
        return self.db.fetchone(
            """
            SELECT * FROM holidays
            WHERE holiday_date <= ? AND end_date >= ? AND is_full_close = 1
            ORDER BY id DESC LIMIT 1;
            """,
            (target_date[:10], target_date[:10]),
        )

    def list_holidays(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses = ["1=1"]
        params: List[Any] = []
        if date_from:
            clauses.append("end_date >= ?")
            params.append(date_from[:10])
        if date_to:
            clauses.append("holiday_date <= ?")
            params.append(date_to[:10])
        params.append(limit)
        return self.db.fetchall(
            f"SELECT * FROM holidays WHERE {' AND '.join(clauses)} ORDER BY holiday_date ASC LIMIT ?;",
            params,
        )
