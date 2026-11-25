from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import DatabaseManager
from utils.dates import now_iso


class GradeRepository:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_evaluation_type(self, eval_id: int) -> Optional[Dict[str, Any]]:
        return self.db.fetchone("SELECT * FROM evaluation_types WHERE id = ?;", (eval_id,))

    def list_evaluation_types(self, active_only: bool = True) -> List[Dict[str, Any]]:
        where = "WHERE is_active = 1" if active_only else ""
        return self.db.fetchall(f"SELECT * FROM evaluation_types {where} ORDER BY id ASC;")

    def create_evaluation_type(
        self,
        name: str,
        term: str = "ترم اول",
        min_score: float = 0.0,
        max_score: float = 20.0,
        weight: float = 1.0,
    ) -> Dict[str, Any]:
        eval_id = self.db.execute(
            """
            INSERT INTO evaluation_types(name, term, min_score, max_score, weight, is_active)
            VALUES (?, ?, ?, ?, ?, 1);
            """,
            (name.strip(), term.strip(), float(min_score), float(max_score), float(weight)),
        )
        return self.get_evaluation_type(eval_id)

    def get_by_id(self, grade_id: int, include_deleted: bool = False) -> Optional[Dict[str, Any]]:
        del_clause = "" if include_deleted else "AND g.is_deleted = 0"
        return self.db.fetchone(
            f"""
            SELECT g.*, stu.full_name AS student_name, stu.student_code,
                   c.name AS class_name, s.name AS subject_name,
                   et.name AS evaluation_name, et.weight AS evaluation_weight,
                   t.full_name AS teacher_name
            FROM grades g
            JOIN users stu ON stu.id = g.student_user_id
            JOIN classes c ON c.id = g.class_id
            JOIN subjects s ON s.id = g.subject_id
            JOIN evaluation_types et ON et.id = g.evaluation_type_id
            LEFT JOIN users t ON t.id = g.teacher_user_id
            WHERE g.id = ? {del_clause};
            """,
            (grade_id,),
        )

    def create_grade(
        self,
        student_user_id: int,
        class_id: int,
        subject_id: int,
        teacher_user_id: Optional[int],
        evaluation_type_id: int,
        academic_term: str,
        score: float,
        min_score: float,
        max_score: float,
        coefficient: float,
        recorded_by: int,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        ts = now_iso()
        with self.db.transaction() as conn:
            dup = conn.execute(
                """
                SELECT id FROM grades
                WHERE student_user_id = ? AND class_id = ? AND subject_id = ?
                  AND evaluation_type_id = ? AND academic_term = ? AND is_deleted = 0;
                """,
                (student_user_id, class_id, subject_id, evaluation_type_id, academic_term),
            ).fetchone()
            if dup:
                raise ValueError("برای این دانش‌آموز در این درس و نوع ارزیابی قبلاً نمره ثبت شده است؛ از ویرایش نمره استفاده کنید.")
            cur = conn.execute(
                """
                INSERT INTO grades(
                    student_user_id, class_id, subject_id, teacher_user_id,
                    evaluation_type_id, academic_term, score, min_score, max_score,
                    coefficient, note, is_deleted, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?);
                """,
                (
                    student_user_id,
                    class_id,
                    subject_id,
                    teacher_user_id,
                    evaluation_type_id,
                    academic_term,
                    score,
                    min_score,
                    max_score,
                    coefficient,
                    note,
                    ts,
                    ts,
                ),
            )
            gid = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO grade_history(grade_id, action, old_score, new_score, reason, changed_by, created_at)
                VALUES (?, 'CREATE', NULL, ?, 'ثبت اولیه نمره', ?, ?);
                """,
                (gid, score, recorded_by, ts),
            )
        return self.get_by_id(gid)

    def update_grade(
        self,
        grade_id: int,
        new_score: float,
        changed_by: int,
        reason: str = "اصلاح نمره",
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        current = self.get_by_id(grade_id)
        if not current:
            raise ValueError("نمره موردنظر یافت نشد.")
        if new_score < current["min_score"] or new_score > current["max_score"]:
            raise ValueError(f"نمره جدید باید بین {current['min_score']} و {current['max_score']} باشد.")
        ts = now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE grades SET score = ?, note = COALESCE(?, note), updated_at = ? WHERE id = ?;",
                (new_score, note, ts, grade_id),
            )
            conn.execute(
                """
                INSERT INTO grade_history(grade_id, action, old_score, new_score, reason, changed_by, created_at)
                VALUES (?, 'UPDATE', ?, ?, ?, ?, ?);
                """,
                (grade_id, current["score"], new_score, reason, changed_by, ts),
            )
        return self.get_by_id(grade_id)

    def soft_delete_grade(self, grade_id: int, changed_by: int, reason: str = "حذف کنترل‌شده") -> Dict[str, Any]:
        current = self.get_by_id(grade_id)
        if not current:
            raise ValueError("نمره موردنظر یافت نشد.")
        ts = now_iso()
        with self.db.transaction() as conn:
            conn.execute("UPDATE grades SET is_deleted = 1, updated_at = ? WHERE id = ?;", (ts, grade_id))
            conn.execute(
                """
                INSERT INTO grade_history(grade_id, action, old_score, new_score, reason, changed_by, created_at)
                VALUES (?, 'DELETE', ?, NULL, ?, ?, ?);
                """,
                (grade_id, current["score"], reason, changed_by, ts),
            )
        return current

    def list_grades(
        self,
        student_user_id: Optional[int] = None,
        class_id: Optional[int] = None,
        subject_id: Optional[int] = None,
        teacher_user_id: Optional[int] = None,
        academic_term: Optional[str] = None,
        limit: int = 300,
    ) -> List[Dict[str, Any]]:
        clauses = ["g.is_deleted = 0"]
        params: List[Any] = []
        if student_user_id is not None:
            clauses.append("g.student_user_id = ?")
            params.append(student_user_id)
        if class_id is not None:
            clauses.append("g.class_id = ?")
            params.append(class_id)
        if subject_id is not None:
            clauses.append("g.subject_id = ?")
            params.append(subject_id)
        if teacher_user_id is not None:
            clauses.append("g.teacher_user_id = ?")
            params.append(teacher_user_id)
        if academic_term:
            clauses.append("g.academic_term = ?")
            params.append(academic_term)
        params.append(max(1, min(limit, 1000)))
        return self.db.fetchall(
            f"""
            SELECT g.*, stu.full_name AS student_name, stu.student_code,
                   c.name AS class_name, s.name AS subject_name,
                   et.name AS evaluation_name, et.weight AS evaluation_weight,
                   t.full_name AS teacher_name
            FROM grades g
            JOIN users stu ON stu.id = g.student_user_id
            JOIN classes c ON c.id = g.class_id
            JOIN subjects s ON s.id = g.subject_id
            JOIN evaluation_types et ON et.id = g.evaluation_type_id
            LEFT JOIN users t ON t.id = g.teacher_user_id
            WHERE {' AND '.join(clauses)}
            ORDER BY s.name ASC, stu.full_name ASC, g.id DESC
            LIMIT ?;
            """,
            params,
        )

    def list_history(self, grade_id: Optional[int] = None, limit: int = 100) -> List[Dict[str, Any]]:
        if grade_id is not None:
            return self.db.fetchall(
                """
                SELECT gh.*, u.full_name AS actor_name, stu.full_name AS student_name, s.name AS subject_name
                FROM grade_history gh
                JOIN grades g ON g.id = gh.grade_id
                JOIN users stu ON stu.id = g.student_user_id
                JOIN subjects s ON s.id = g.subject_id
                JOIN users u ON u.id = gh.changed_by
                WHERE gh.grade_id = ?
                ORDER BY gh.id ASC;
                """,
                (grade_id,),
            )
        return self.db.fetchall(
            """
            SELECT gh.*, u.full_name AS actor_name, stu.full_name AS student_name, s.name AS subject_name
            FROM grade_history gh
            JOIN grades g ON g.id = gh.grade_id
            JOIN users stu ON stu.id = g.student_user_id
            JOIN subjects s ON s.id = g.subject_id
            JOIN users u ON u.id = gh.changed_by
            ORDER BY gh.id DESC LIMIT ?;
            """,
            (limit,),
        )

    # تکالیف آموزشی کلاس
    def create_homework(
        self,
        class_id: int,
        subject_id: int,
        teacher_user_id: int,
        title: str,
        description: str,
        due_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        hid = self.db.execute(
            """
            INSERT INTO homework_items(class_id, subject_id, teacher_user_id, title, description, due_date, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?);
            """,
            (class_id, subject_id, teacher_user_id, title.strip(), description.strip(), due_date, now_iso()),
        )
        return self.db.fetchone(
            """
            SELECT h.*, s.name AS subject_name, c.name AS class_name, u.full_name AS teacher_name
            FROM homework_items h
            JOIN subjects s ON s.id = h.subject_id
            JOIN classes c ON c.id = h.class_id
            JOIN users u ON u.id = h.teacher_user_id
            WHERE h.id = ?;
            """,
            (hid,),
        )

    def list_homework(self, class_id: int, limit: int = 30) -> List[Dict[str, Any]]:
        return self.db.fetchall(
            """
            SELECT h.*, s.name AS subject_name, c.name AS class_name, u.full_name AS teacher_name
            FROM homework_items h
            JOIN subjects s ON s.id = h.subject_id
            JOIN classes c ON c.id = h.class_id
            JOIN users u ON u.id = h.teacher_user_id
            WHERE h.class_id = ? AND h.is_active = 1
            ORDER BY h.id DESC LIMIT ?;
            """,
            (class_id, limit),
        )
