from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import DatabaseManager
from utils.dates import now_iso


class RegistrationRepository:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_by_id(self, req_id: int) -> Optional[Dict[str, Any]]:
        return self.db.fetchone(
            """
            SELECT r.*, u.full_name AS reviewer_name
            FROM registration_requests r
            LEFT JOIN users u ON u.id = r.reviewed_by
            WHERE r.id = ?;
            """,
            (req_id,),
        )

    def get_pending_by_bale_id(self, bale_user_id: int) -> Optional[Dict[str, Any]]:
        return self.db.fetchone(
            "SELECT * FROM registration_requests WHERE bale_user_id = ? AND status = 'PENDING';",
            (bale_user_id,),
        )

    def create_request(
        self,
        bale_user_id: int,
        full_name: str,
        requested_role: str,
        phone: Optional[str] = None,
        grade_level: Optional[str] = None,
        requested_class_name: Optional[str] = None,
        student_code: Optional[str] = None,
        parent_phone: Optional[str] = None,
        target_student_code: Optional[str] = None,
        specialty: Optional[str] = None,
    ) -> Dict[str, Any]:
        ts = now_iso()
        req_id = self.db.execute(
            """
            INSERT INTO registration_requests(
                bale_user_id, full_name, requested_role, phone,
                grade_level, requested_class_name, student_code,
                parent_phone, target_student_code, specialty,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?);
            """,
            (
                bale_user_id,
                full_name,
                requested_role,
                phone,
                grade_level,
                requested_class_name,
                student_code,
                parent_phone,
                target_student_code,
                specialty,
                ts,
                ts,
            ),
        )
        return self.get_by_id(req_id)

    def set_mgmt_message_id(self, req_id: int, message_id: int) -> None:
        self.db.execute(
            "UPDATE registration_requests SET mgmt_message_id = ?, updated_at = ? WHERE id = ?;",
            (message_id, now_iso(), req_id),
        )

    def list_requests(self, status: Optional[str] = "PENDING", limit: int = 100) -> List[Dict[str, Any]]:
        if status:
            return self.db.fetchall(
                """
                SELECT r.*, u.full_name AS reviewer_name
                FROM registration_requests r
                LEFT JOIN users u ON u.id = r.reviewed_by
                WHERE r.status = ?
                ORDER BY r.id ASC
                LIMIT ?;
                """,
                (status.upper(), limit),
            )
        return self.db.fetchall(
            """
            SELECT r.*, u.full_name AS reviewer_name
            FROM registration_requests r
            LEFT JOIN users u ON u.id = r.reviewed_by
            ORDER BY r.id DESC
            LIMIT ?;
            """,
            (limit,),
        )

    def cancel_by_user(self, req_id: int, bale_user_id: int) -> bool:
        ts = now_iso()
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE registration_requests
                SET status = 'CANCELLED', updated_at = ?
                WHERE id = ? AND bale_user_id = ? AND status = 'PENDING';
                """,
                (ts, req_id, bale_user_id),
            )
            return cur.rowcount == 1

    def transition_status_atomic(
        self,
        req_id: int,
        new_status: str,
        reviewer_user_id: int,
        rejection_reason: Optional[str] = None,
    ) -> bool:
        """تغییر وضعیت اتمیک فقط از حالت PENDING برای جلوگیری قطعی از تأیید یا رد چندباره."""
        ts = now_iso()
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE registration_requests
                SET status = ?, reviewed_by = ?, reviewed_at = ?, rejection_reason = ?, updated_at = ?
                WHERE id = ? AND status = 'PENDING';
                """,
                (new_status, reviewer_user_id, ts, rejection_reason, ts, req_id),
            )
            return cur.rowcount == 1
