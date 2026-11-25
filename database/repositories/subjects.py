from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import DatabaseManager
from utils.dates import now_iso


class SubjectRepository:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_by_id(self, subject_id: int) -> Optional[Dict[str, Any]]:
        return self.db.fetchone("SELECT * FROM subjects WHERE id = ?;", (subject_id,))

    def create(
        self,
        name: str,
        grade_level: str = "عمومی",
        subject_type: str = "عمومی",
        code: Optional[str] = None,
        default_weekly_periods: int = 2,
        default_coefficient: float = 1.0,
    ) -> Dict[str, Any]:
        subj_id = self.db.execute(
            """
            INSERT INTO subjects(
                name, code, grade_level, subject_type,
                default_weekly_periods, default_coefficient, is_active, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?);
            """,
            (
                name.strip(),
                code.strip() if code else None,
                grade_level.strip(),
                subject_type.strip(),
                int(default_weekly_periods),
                float(default_coefficient),
                now_iso(),
            ),
        )
        return self.get_by_id(subj_id)

    def update(self, subject_id: int, **fields: Any) -> Optional[Dict[str, Any]]:
        allowed = {
            "name",
            "code",
            "grade_level",
            "subject_type",
            "default_weekly_periods",
            "default_coefficient",
            "is_active",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_by_id(subject_id)
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        self.db.execute(f"UPDATE subjects SET {set_clause} WHERE id = ?;", list(updates.values()) + [subject_id])
        return self.get_by_id(subject_id)

    def controlled_delete(self, subject_id: int) -> str:
        grades_cnt = self.db.fetchone("SELECT COUNT(*) AS c FROM grades WHERE subject_id = ?;", (subject_id,))["c"]
        tt_cnt = self.db.fetchone("SELECT COUNT(*) AS c FROM timetable_entries WHERE subject_id = ?;", (subject_id,))["c"]
        cs_cnt = self.db.fetchone("SELECT COUNT(*) AS c FROM class_subjects WHERE subject_id = ?;", (subject_id,))["c"]
        hw_cnt = self.db.fetchone("SELECT COUNT(*) AS c FROM homework_items WHERE subject_id = ?;", (subject_id,))["c"]
        if grades_cnt > 0 or tt_cnt > 0 or cs_cnt > 0 or hw_cnt > 0:
            self.update(subject_id, is_active=0)
            self.db.execute("UPDATE class_subjects SET is_active = 0 WHERE subject_id = ?;", (subject_id,))
            return "ARCHIVED"
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM subjects WHERE id = ?;", (subject_id,))
        return "DELETED"

    def list_subjects(self, active_only: bool = False, grade_level: Optional[str] = None) -> List[Dict[str, Any]]:
        clauses = ["1=1"]
        params: List[Any] = []
        if active_only:
            clauses.append("is_active = 1")
        if grade_level:
            clauses.append("(grade_level = ? OR grade_level = 'عمومی')")
            params.append(grade_level.strip())
        return self.db.fetchall(
            f"SELECT * FROM subjects WHERE {' AND '.join(clauses)} ORDER BY grade_level, name;",
            params,
        )

    def assign_to_class(
        self,
        class_id: int,
        subject_id: int,
        teacher_user_id: Optional[int],
        weekly_periods: int = 2,
        coefficient: float = 1.0,
    ) -> Dict[str, Any]:
        ts = now_iso()
        existing = self.db.fetchone(
            "SELECT * FROM class_subjects WHERE class_id = ? AND subject_id = ?;",
            (class_id, subject_id),
        )
        if existing and existing["is_active"]:
            raise ValueError("این درس قبلاً به این کلاس تخصیص داده شده است.")
        if existing:
            self.db.execute(
                """
                UPDATE class_subjects
                SET teacher_user_id = ?, weekly_periods = ?, coefficient = ?, is_active = 1
                WHERE id = ?;
                """,
                (teacher_user_id, weekly_periods, coefficient, existing["id"]),
            )
            cs_id = existing["id"]
        else:
            cs_id = self.db.execute(
                """
                INSERT INTO class_subjects(class_id, subject_id, teacher_user_id, weekly_periods, coefficient, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?);
                """,
                (class_id, subject_id, teacher_user_id, weekly_periods, coefficient, ts),
            )
        return self.db.fetchone("SELECT * FROM class_subjects WHERE id = ?;", (cs_id,))

    def update_class_assignment(
        self,
        class_id: int,
        subject_id: int,
        teacher_user_id: Optional[int],
        weekly_periods: int,
        coefficient: float,
    ) -> None:
        self.db.execute(
            """
            UPDATE class_subjects
            SET teacher_user_id = ?, weekly_periods = ?, coefficient = ?, is_active = 1
            WHERE class_id = ? AND subject_id = ?;
            """,
            (teacher_user_id, weekly_periods, coefficient, class_id, subject_id),
        )

    def unassign_from_class(self, class_id: int, subject_id: int) -> None:
        self.db.execute(
            "UPDATE class_subjects SET is_active = 0 WHERE class_id = ? AND subject_id = ?;",
            (class_id, subject_id),
        )

    def list_class_subjects(self, class_id: int) -> List[Dict[str, Any]]:
        return self.db.fetchall(
            """
            SELECT cs.*, s.name AS subject_name, s.subject_type, s.code AS subject_code,
                   u.full_name AS teacher_name, c.name AS class_name
            FROM class_subjects cs
            JOIN subjects s ON s.id = cs.subject_id
            JOIN classes c ON c.id = cs.class_id
            LEFT JOIN users u ON u.id = cs.teacher_user_id
            WHERE cs.class_id = ? AND cs.is_active = 1 AND s.is_active = 1
            ORDER BY s.name;
            """,
            (class_id,),
        )

    def list_teacher_subjects(self, teacher_user_id: int) -> List[Dict[str, Any]]:
        return self.db.fetchall(
            """
            SELECT cs.*, s.name AS subject_name, s.subject_type,
                   c.name AS class_name, c.grade_level, c.academic_year
            FROM class_subjects cs
            JOIN subjects s ON s.id = cs.subject_id
            JOIN classes c ON c.id = cs.class_id
            WHERE cs.teacher_user_id = ? AND cs.is_active = 1 AND c.is_active = 1 AND s.is_active = 1
            ORDER BY c.grade_level, c.name, s.name;
            """,
            (teacher_user_id,),
        )

    def is_teacher_assigned(self, teacher_user_id: int, class_id: int, subject_id: Optional[int] = None) -> bool:
        if subject_id is not None:
            row = self.db.fetchone(
                """
                SELECT 1 FROM class_subjects
                WHERE teacher_user_id = ? AND class_id = ? AND subject_id = ? AND is_active = 1;
                """,
                (teacher_user_id, class_id, subject_id),
            )
            return row is not None
        row = self.db.fetchone(
            "SELECT 1 FROM class_subjects WHERE teacher_user_id = ? AND class_id = ? AND is_active = 1;",
            (teacher_user_id, class_id),
        )
        return row is not None
