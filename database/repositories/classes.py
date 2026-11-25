from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import DatabaseManager
from utils.dates import now_iso


class ClassRepository:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_by_id(self, class_id: int) -> Optional[Dict[str, Any]]:
        return self.db.fetchone(
            """
            SELECT c.*,
                   (SELECT COUNT(*) FROM class_enrollments ce
                    JOIN users u ON u.id = ce.student_user_id
                    WHERE ce.class_id = c.id AND ce.is_active = 1 AND u.is_active = 1) AS active_student_count
            FROM classes c
            WHERE c.id = ?;
            """,
            (class_id,),
        )

    def get_by_name_and_year(self, name: str, academic_year: str) -> Optional[Dict[str, Any]]:
        return self.db.fetchone(
            "SELECT * FROM classes WHERE name = ? AND academic_year = ?;",
            (name.strip(), academic_year.strip()),
        )

    def create(
        self,
        name: str,
        grade_level: str,
        academic_year: str,
        capacity: int = 35,
        changed_by: Optional[int] = None,
    ) -> Dict[str, Any]:
        ts = now_iso()
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO classes(name, grade_level, academic_year, capacity, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?);
                """,
                (name.strip(), grade_level.strip(), academic_year.strip(), capacity, ts, ts),
            )
            class_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO class_history(class_id, action, changed_by, note, created_at)
                VALUES (?, 'CREATE_CLASS', ?, ?, ?);
                """,
                (class_id, changed_by, f"ایجاد کلاس {name} ({academic_year})", ts),
            )
        return self.get_by_id(class_id)

    def update(
        self,
        class_id: int,
        name: Optional[str] = None,
        grade_level: Optional[str] = None,
        academic_year: Optional[str] = None,
        capacity: Optional[int] = None,
        is_active: Optional[int] = None,
        changed_by: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        current = self.get_by_id(class_id)
        if not current:
            return None
        ts = now_iso()
        new_name = name.strip() if name is not None else current["name"]
        new_grade = grade_level.strip() if grade_level is not None else current["grade_level"]
        new_year = academic_year.strip() if academic_year is not None else current["academic_year"]
        new_cap = int(capacity) if capacity is not None else current["capacity"]
        new_active = int(bool(is_active)) if is_active is not None else current["is_active"]

        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE classes
                SET name = ?, grade_level = ?, academic_year = ?, capacity = ?, is_active = ?, updated_at = ?
                WHERE id = ?;
                """,
                (new_name, new_grade, new_year, new_cap, new_active, ts, class_id),
            )
            conn.execute(
                """
                INSERT INTO class_history(class_id, action, changed_by, note, created_at)
                VALUES (?, 'UPDATE_CLASS', ?, ?, ?);
                """,
                (class_id, changed_by, f"ویرایش کلاس {new_name} (فعال={new_active})", ts),
            )
        return self.get_by_id(class_id)

    def has_historical_records(self, class_id: int) -> Dict[str, int]:
        enroll_cnt = self.db.fetchone("SELECT COUNT(*) AS c FROM class_enrollments WHERE class_id = ?;", (class_id,))["c"]
        att_cnt = self.db.fetchone("SELECT COUNT(*) AS c FROM attendance_records WHERE class_id = ?;", (class_id,))["c"]
        grade_cnt = self.db.fetchone("SELECT COUNT(*) AS c FROM grades WHERE class_id = ?;", (class_id,))["c"]
        return {"enrollments": int(enroll_cnt), "attendance": int(att_cnt), "grades": int(grade_cnt)}

    def controlled_delete_or_archive(self, class_id: int, changed_by: Optional[int] = None) -> Dict[str, Any]:
        """اگر کلاس سابقه تاریخی داشته باشد برای حفاظت از سوابق غیرفعال می‌شود؛ در غیر این صورت حذف می‌گردد."""
        history = self.has_historical_records(class_id)
        total_refs = sum(history.values())
        ts = now_iso()
        if total_refs > 0:
            self.update(class_id, is_active=0, changed_by=changed_by)
            return {"mode": "ARCHIVED", "references": history}
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO class_history(class_id, action, changed_by, note, created_at)
                VALUES (NULL, 'DELETE_CLASS', ?, ?, ?);
                """,
                (changed_by, f"حذف کامل کلاس بدون سابقه (شناسه {class_id})", ts),
            )
            conn.execute("DELETE FROM class_subjects WHERE class_id = ?;", (class_id,))
            conn.execute("DELETE FROM classes WHERE id = ?;", (class_id,))
        return {"mode": "DELETED", "references": history}

    def list_classes(
        self,
        query: Optional[str] = None,
        academic_year: Optional[str] = None,
        active_only: bool = False,
    ) -> List[Dict[str, Any]]:
        clauses = ["1=1"]
        params: List[Any] = []
        if query:
            q = f"%{query.strip()}%"
            clauses.append("(c.name LIKE ? OR c.grade_level LIKE ?)")
            params.extend([q, q])
        if academic_year:
            clauses.append("c.academic_year = ?")
            params.append(academic_year.strip())
        if active_only:
            clauses.append("c.is_active = 1")
        return self.db.fetchall(
            f"""
            SELECT c.*,
                   (SELECT COUNT(*) FROM class_enrollments ce
                    JOIN users u ON u.id = ce.student_user_id
                    WHERE ce.class_id = c.id AND ce.is_active = 1 AND u.is_active = 1) AS active_student_count
            FROM classes c
            WHERE {' AND '.join(clauses)}
            ORDER BY c.grade_level, c.name;
            """,
            params,
        )

    def get_student_active_class(self, student_user_id: int, academic_year: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if academic_year:
            return self.db.fetchone(
                """
                SELECT c.*, ce.enrolled_at
                FROM class_enrollments ce
                JOIN classes c ON c.id = ce.class_id
                WHERE ce.student_user_id = ? AND ce.academic_year = ? AND ce.is_active = 1 AND c.is_active = 1;
                """,
                (student_user_id, academic_year),
            )
        return self.db.fetchone(
            """
            SELECT c.*, ce.enrolled_at
            FROM class_enrollments ce
            JOIN classes c ON c.id = ce.class_id
            WHERE ce.student_user_id = ? AND ce.is_active = 1 AND c.is_active = 1
            ORDER BY ce.id DESC LIMIT 1;
            """,
            (student_user_id,),
        )

    def enroll_student(self, class_id: int, student_user_id: int, changed_by: Optional[int] = None) -> None:
        cls = self.get_by_id(class_id)
        if not cls or not cls["is_active"]:
            raise ValueError("کلاس انتخاب‌شده فعال یا موجود نیست.")
        ts = now_iso()
        with self.db.transaction() as conn:
            existing = conn.execute(
                """
                SELECT * FROM class_enrollments
                WHERE student_user_id = ? AND academic_year = ? AND is_active = 1;
                """,
                (student_user_id, cls["academic_year"]),
            ).fetchone()
            if existing:
                if existing["class_id"] == class_id:
                    raise ValueError("این دانش‌آموز هم‌اکنون در همین کلاس ثبت‌شده است.")
                raise ValueError("دانش‌آموز در این سال تحصیلی در کلاس دیگری فعال است؛ از قابلیت انتقال کلاس استفاده کنید.")
            conn.execute(
                """
                INSERT INTO class_enrollments(class_id, student_user_id, academic_year, is_active, enrolled_at)
                VALUES (?, ?, ?, 1, ?);
                """,
                (class_id, student_user_id, cls["academic_year"], ts),
            )
            conn.execute(
                """
                INSERT INTO class_history(class_id, to_class_id, student_user_id, action, changed_by, note, created_at)
                VALUES (?, ?, ?, 'ENROLL_STUDENT', ?, ?, ?);
                """,
                (class_id, class_id, student_user_id, changed_by, f"ثبت دانش‌آموز در کلاس {cls['name']}", ts),
            )

    def transfer_student(
        self,
        student_user_id: int,
        to_class_id: int,
        changed_by: Optional[int] = None,
        note: str = "",
    ) -> Dict[str, Any]:
        target_cls = self.get_by_id(to_class_id)
        if not target_cls or not target_cls["is_active"]:
            raise ValueError("کلاس مقصد فعال یا معتبر نیست.")
        ts = now_iso()
        with self.db.transaction() as conn:
            current = conn.execute(
                """
                SELECT * FROM class_enrollments
                WHERE student_user_id = ? AND academic_year = ? AND is_active = 1;
                """,
                (student_user_id, target_cls["academic_year"]),
            ).fetchone()
            from_class_id = current["class_id"] if current else None
            if from_class_id == to_class_id:
                raise ValueError("کلاس مبدأ و مقصد یکسان است.")
            if current:
                conn.execute(
                    "UPDATE class_enrollments SET is_active = 0, left_at = ? WHERE id = ?;",
                    (ts, current["id"]),
                )
            conn.execute(
                """
                INSERT INTO class_enrollments(class_id, student_user_id, academic_year, is_active, enrolled_at)
                VALUES (?, ?, ?, 1, ?);
                """,
                (to_class_id, student_user_id, target_cls["academic_year"], ts),
            )
            conn.execute(
                """
                INSERT INTO class_history(
                    class_id, from_class_id, to_class_id, student_user_id, action, changed_by, note, created_at
                ) VALUES (?, ?, ?, ?, 'TRANSFER_STUDENT', ?, ?, ?);
                """,
                (
                    to_class_id,
                    from_class_id,
                    to_class_id,
                    student_user_id,
                    changed_by,
                    note or f"انتقال به کلاس {target_cls['name']}",
                    ts,
                ),
            )
        return {"from_class_id": from_class_id, "to_class_id": to_class_id}

    def list_class_students(self, class_id: int, active_users_only: bool = True) -> List[Dict[str, Any]]:
        user_filter = "AND u.is_active = 1" if active_users_only else ""
        return self.db.fetchall(
            f"""
            SELECT u.*, ce.enrolled_at
            FROM class_enrollments ce
            JOIN users u ON u.id = ce.student_user_id
            WHERE ce.class_id = ? AND ce.is_active = 1 AND u.role = 'STUDENT' {user_filter}
            ORDER BY u.full_name;
            """,
            (class_id,),
        )

    def list_teacher_classes(self, teacher_user_id: int) -> List[Dict[str, Any]]:
        return self.db.fetchall(
            """
            SELECT DISTINCT c.*,
                   (SELECT COUNT(*) FROM class_enrollments ce
                    JOIN users u ON u.id = ce.student_user_id
                    WHERE ce.class_id = c.id AND ce.is_active = 1 AND u.is_active = 1) AS active_student_count
            FROM classes c
            JOIN class_subjects cs ON cs.class_id = c.id
            WHERE cs.teacher_user_id = ? AND cs.is_active = 1 AND c.is_active = 1
            ORDER BY c.grade_level, c.name;
            """,
            (teacher_user_id,),
        )

    def get_history(self, class_id: Optional[int] = None, student_user_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
        clauses = ["1=1"]
        params: List[Any] = []
        if class_id is not None:
            clauses.append("(ch.class_id = ? OR ch.from_class_id = ? OR ch.to_class_id = ?)")
            params.extend([class_id, class_id, class_id])
        if student_user_id is not None:
            clauses.append("ch.student_user_id = ?")
            params.append(student_user_id)
        params.append(limit)
        return self.db.fetchall(
            f"""
            SELECT ch.*, u.full_name AS student_name, actor.full_name AS actor_name
            FROM class_history ch
            LEFT JOIN users u ON u.id = ch.student_user_id
            LEFT JOIN users actor ON actor.id = ch.changed_by
            WHERE {' AND '.join(clauses)}
            ORDER BY ch.id DESC LIMIT ?;
            """,
            params,
        )
