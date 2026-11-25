from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import DatabaseManager
from utils.dates import now_iso


class AttendanceRepository:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_by_id(self, attendance_id: int) -> Optional[Dict[str, Any]]:
        return self.db.fetchone(
            """
            SELECT ar.*, u.full_name AS student_name, u.student_code,
                   c.name AS class_name, rec.full_name AS recorder_name
            FROM attendance_records ar
            JOIN users u ON u.id = ar.student_user_id
            JOIN classes c ON c.id = ar.class_id
            JOIN users rec ON rec.id = ar.recorded_by
            WHERE ar.id = ?;
            """,
            (attendance_id,),
        )

    def get_slot_record(self, class_id: int, student_user_id: int, att_date: str, period_number: int) -> Optional[Dict[str, Any]]:
        return self.db.fetchone(
            """
            SELECT * FROM attendance_records
            WHERE class_id = ? AND student_user_id = ? AND att_date = ? AND period_number = ?;
            """,
            (class_id, student_user_id, att_date, period_number),
        )

    def create(
        self,
        class_id: int,
        student_user_id: int,
        att_date: str,
        period_number: int,
        status: str,
        recorded_by: int,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        ts = now_iso()
        with self.db.transaction() as conn:
            dup = conn.execute(
                """
                SELECT id FROM attendance_records
                WHERE class_id = ? AND student_user_id = ? AND att_date = ? AND period_number = ?;
                """,
                (class_id, student_user_id, att_date, period_number),
            ).fetchone()
            if dup:
                raise ValueError("برای این دانش‌آموز در این تاریخ و زنگ قبلاً حضور و غیاب ثبت شده است.")
            cur = conn.execute(
                """
                INSERT INTO attendance_records(
                    class_id, student_user_id, att_date, period_number,
                    status, note, recorded_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (class_id, student_user_id, att_date, period_number, status, note, recorded_by, ts, ts),
            )
            att_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO attendance_history(attendance_id, old_status, new_status, reason, changed_by, created_at)
                VALUES (?, NULL, ?, 'ثبت اولیه', ?, ?);
                """,
                (att_id, status, recorded_by, ts),
            )
        return self.get_by_id(att_id)

    def update_status(
        self,
        attendance_id: int,
        new_status: str,
        changed_by: int,
        reason: str,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        current = self.get_by_id(attendance_id)
        if not current:
            raise ValueError("رکورد حضور و غیاب موردنظر یافت نشد.")
        ts = now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE attendance_records
                SET status = ?, note = COALESCE(?, note), updated_by = ?, update_reason = ?, updated_at = ?
                WHERE id = ?;
                """,
                (new_status, note, changed_by, reason, ts, attendance_id),
            )
            conn.execute(
                """
                INSERT INTO attendance_history(attendance_id, old_status, new_status, reason, changed_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (attendance_id, current["status"], new_status, reason, changed_by, ts),
            )
        return self.get_by_id(attendance_id)

    def list_records(
        self,
        class_id: Optional[int] = None,
        student_user_id: Optional[int] = None,
        recorded_by: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        period_number: Optional[int] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        clauses = ["1=1"]
        params: List[Any] = []
        if class_id is not None:
            clauses.append("ar.class_id = ?")
            params.append(class_id)
        if student_user_id is not None:
            clauses.append("ar.student_user_id = ?")
            params.append(student_user_id)
        if recorded_by is not None:
            clauses.append("ar.recorded_by = ?")
            params.append(recorded_by)
        if date_from:
            clauses.append("ar.att_date >= ?")
            params.append(date_from[:10])
        if date_to:
            clauses.append("ar.att_date <= ?")
            params.append(date_to[:10])
        if period_number is not None:
            clauses.append("ar.period_number = ?")
            params.append(period_number)
        params.append(max(1, min(limit, 1000)))
        return self.db.fetchall(
            f"""
            SELECT ar.*, u.full_name AS student_name, u.student_code,
                   c.name AS class_name, rec.full_name AS recorder_name
            FROM attendance_records ar
            JOIN users u ON u.id = ar.student_user_id
            JOIN classes c ON c.id = ar.class_id
            JOIN users rec ON rec.id = ar.recorded_by
            WHERE {' AND '.join(clauses)}
            ORDER BY ar.att_date DESC, ar.period_number ASC, u.full_name ASC
            LIMIT ?;
            """,
            params,
        )

    def summarize_status_counts(
        self,
        class_id: Optional[int] = None,
        student_user_id: Optional[int] = None,
        recorded_by: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, int]:
        clauses = ["1=1"]
        params: List[Any] = []
        if class_id is not None:
            clauses.append("class_id = ?")
            params.append(class_id)
        if student_user_id is not None:
            clauses.append("student_user_id = ?")
            params.append(student_user_id)
        if recorded_by is not None:
            clauses.append("recorded_by = ?")
            params.append(recorded_by)
        if date_from:
            clauses.append("att_date >= ?")
            params.append(date_from[:10])
        if date_to:
            clauses.append("att_date <= ?")
            params.append(date_to[:10])
        rows = self.db.fetchall(
            f"""
            SELECT status, COUNT(*) AS cnt
            FROM attendance_records
            WHERE {' AND '.join(clauses)}
            GROUP BY status;
            """,
            params,
        )
        base = {
            "PRESENT": 0,
            "ABSENT": 0,
            "LATE": 0,
            "EARLY_LEAVE": 0,
            "EXCUSED_ABSENT": 0,
            "UNEXCUSED_ABSENT": 0,
        }
        for r in rows:
            base[r["status"]] = int(r["cnt"])
        base["TOTAL"] = sum(base.values())
        return base

    def get_history(self, attendance_id: int) -> List[Dict[str, Any]]:
        return self.db.fetchall(
            """
            SELECT ah.*, u.full_name AS actor_name
            FROM attendance_history ah
            JOIN users u ON u.id = ah.changed_by
            WHERE ah.attendance_id = ?
            ORDER BY ah.id ASC;
            """,
            (attendance_id,),
        )

    def add_disciplinary_note(
        self,
        student_user_id: int,
        class_id: Optional[int],
        note_date: str,
        category: str,
        description: str,
        recorded_by: int,
    ) -> Dict[str, Any]:
        nid = self.db.execute(
            """
            INSERT INTO disciplinary_notes(student_user_id, class_id, note_date, category, description, recorded_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (student_user_id, class_id, note_date, category, description, recorded_by, now_iso()),
        )
        return self.db.fetchone("SELECT * FROM disciplinary_notes WHERE id = ?;", (nid,))

    def list_disciplinary_notes(self, student_user_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
        if student_user_id is not None:
            return self.db.fetchall(
                """
                SELECT dn.*, s.full_name AS student_name, r.full_name AS recorder_name, c.name AS class_name
                FROM disciplinary_notes dn
                JOIN users s ON s.id = dn.student_user_id
                JOIN users r ON r.id = dn.recorded_by
                LEFT JOIN classes c ON c.id = dn.class_id
                WHERE dn.student_user_id = ?
                ORDER BY dn.id DESC LIMIT ?;
                """,
                (student_user_id, limit),
            )
        return self.db.fetchall(
            """
            SELECT dn.*, s.full_name AS student_name, r.full_name AS recorder_name, c.name AS class_name
            FROM disciplinary_notes dn
            JOIN users s ON s.id = dn.student_user_id
            JOIN users r ON r.id = dn.recorded_by
            LEFT JOIN classes c ON c.id = dn.class_id
            ORDER BY dn.id DESC LIMIT ?;
            """,
            (limit,),
        )
