from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from database.db import DatabaseManager
from utils.dates import now_iso


class UserRepository:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        return self.db.fetchone("SELECT * FROM users WHERE id = ?;", (user_id,))

    def get_by_bale_id(self, bale_user_id: int) -> Optional[Dict[str, Any]]:
        return self.db.fetchone("SELECT * FROM users WHERE bale_user_id = ?;", (bale_user_id,))

    def get_by_student_code(self, student_code: str) -> Optional[Dict[str, Any]]:
        return self.db.fetchone("SELECT * FROM users WHERE student_code = ?;", (student_code,))

    def create(
        self,
        bale_user_id: int,
        full_name: str,
        role: str,
        username: Optional[str] = None,
        phone: Optional[str] = None,
        student_code: Optional[str] = None,
        national_code: Optional[str] = None,
        grade_level: Optional[str] = None,
        parent_phone: Optional[str] = None,
        personnel_code: Optional[str] = None,
        specialty: Optional[str] = None,
        web_password_hash: Optional[str] = None,
        is_active: int = 1,
    ) -> Dict[str, Any]:
        ts = now_iso()
        new_id = self.db.execute(
            """
            INSERT INTO users(
                bale_user_id, username, full_name, phone, role,
                student_code, national_code, grade_level, parent_phone,
                personnel_code, specialty, web_password_hash, is_active,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                bale_user_id,
                username,
                full_name,
                phone,
                role,
                student_code,
                national_code,
                grade_level,
                parent_phone,
                personnel_code,
                specialty,
                web_password_hash,
                int(bool(is_active)),
                ts,
                ts,
            ),
        )
        return self.get_by_id(new_id)

    def update_fields(self, user_id: int, **fields: Any) -> Optional[Dict[str, Any]]:
        allowed = {
            "username",
            "full_name",
            "phone",
            "role",
            "student_code",
            "national_code",
            "grade_level",
            "parent_phone",
            "personnel_code",
            "specialty",
            "web_password_hash",
            "custom_permissions",
            "is_active",
            "failed_web_logins",
            "locked_until",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_by_id(user_id)
        updates["updated_at"] = now_iso()
        set_clause = ", ".join(f"{col} = ?" for col in updates)
        params = list(updates.values()) + [user_id]
        self.db.execute(f"UPDATE users SET {set_clause} WHERE id = ?;", params)
        return self.get_by_id(user_id)

    def set_custom_permissions(self, user_id: int, permissions: List[str]) -> Optional[Dict[str, Any]]:
        clean = sorted({str(p).strip().upper() for p in permissions if str(p).strip()})
        return self.update_fields(user_id, custom_permissions=json.dumps(clean))

    def get_custom_permissions(self, user: Dict[str, Any]) -> List[str]:
        raw = user.get("custom_permissions") or "[]"
        try:
            data = json.loads(raw)
            return [str(x).upper() for x in data if isinstance(x, str)]
        except Exception:
            return []

    def search(
        self,
        query: Optional[str] = None,
        role: Optional[str] = None,
        active_only: bool = False,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses = ["1=1"]
        params: List[Any] = []
        if query:
            q = f"%{query.strip()}%"
            clauses.append("(full_name LIKE ? OR CAST(bale_user_id AS TEXT) LIKE ? OR IFNULL(student_code, '') LIKE ? OR IFNULL(phone, '') LIKE ?)")
            params.extend([q, q, q, q])
        if role:
            clauses.append("role = ?")
            params.append(role.upper())
        if active_only:
            clauses.append("is_active = 1")
        params.append(max(1, min(limit, 500)))
        return self.db.fetchall(
            f"SELECT * FROM users WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?;",
            params,
        )

    def count_by_role(self, active_only: bool = True) -> Dict[str, int]:
        where = "WHERE is_active = 1" if active_only else ""
        rows = self.db.fetchall(f"SELECT role, COUNT(*) AS cnt FROM users {where} GROUP BY role;")
        return {r["role"]: int(r["cnt"]) for r in rows}

    def link_parent_student(self, parent_user_id: int, student_user_id: int, relation: str = "والد") -> None:
        self.db.execute(
            """
            INSERT OR IGNORE INTO parent_students(parent_user_id, student_user_id, relation, created_at)
            VALUES (?, ?, ?, ?);
            """,
            (parent_user_id, student_user_id, relation, now_iso()),
        )

    def unlink_parent_student(self, parent_user_id: int, student_user_id: int) -> None:
        self.db.execute(
            "DELETE FROM parent_students WHERE parent_user_id = ? AND student_user_id = ?;",
            (parent_user_id, student_user_id),
        )

    def get_children_of_parent(self, parent_user_id: int) -> List[Dict[str, Any]]:
        return self.db.fetchall(
            """
            SELECT u.*, ps.relation
            FROM parent_students ps
            JOIN users u ON u.id = ps.student_user_id
            WHERE ps.parent_user_id = ? AND u.is_active = 1
            ORDER BY u.full_name;
            """,
            (parent_user_id,),
        )

    def get_parents_of_student(self, student_user_id: int) -> List[Dict[str, Any]]:
        return self.db.fetchall(
            """
            SELECT u.*, ps.relation
            FROM parent_students ps
            JOIN users u ON u.id = ps.parent_user_id
            WHERE ps.student_user_id = ? AND u.is_active = 1
            ORDER BY u.full_name;
            """,
            (student_user_id,),
        )

    # مدیریت وضعیت گفت‌وگوی چندمرحله‌ای کاربران در ربات
    def get_session(self, bale_user_id: int) -> Optional[Dict[str, Any]]:
        row = self.db.fetchone("SELECT * FROM user_sessions WHERE bale_user_id = ?;", (bale_user_id,))
        if not row:
            return None
        try:
            row["data"] = json.loads(row.get("payload") or "{}")
        except Exception:
            row["data"] = {}
        return row

    def set_session(self, bale_user_id: int, flow: str, step: str, data: Optional[Dict[str, Any]] = None) -> None:
        payload = json.dumps(data or {}, ensure_ascii=False)
        ts = now_iso()
        self.db.execute(
            """
            INSERT INTO user_sessions(bale_user_id, flow, step, payload, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(bale_user_id) DO UPDATE SET
                flow = excluded.flow,
                step = excluded.step,
                payload = excluded.payload,
                updated_at = excluded.updated_at;
            """,
            (bale_user_id, flow, step, payload, ts),
        )

    def clear_session(self, bale_user_id: int) -> None:
        self.db.execute("DELETE FROM user_sessions WHERE bale_user_id = ?;", (bale_user_id,))
