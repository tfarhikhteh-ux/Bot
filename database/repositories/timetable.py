from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from database.db import DatabaseManager
from utils.dates import now_iso


def overlapping_week_types(week_type: str) -> Sequence[str]:
    wt = week_type.upper()
    if wt == "BOTH":
        return ("BOTH", "WEEK_1", "WEEK_2")
    if wt == "WEEK_1":
        return ("BOTH", "WEEK_1")
    if wt == "WEEK_2":
        return ("BOTH", "WEEK_2")
    raise ValueError("نوع هفته نامعتبر است.")


class TimetableRepository:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def list_bell_periods(self, active_only: bool = True) -> List[Dict[str, Any]]:
        where = "WHERE is_active = 1" if active_only else ""
        return self.db.fetchall(f"SELECT * FROM bell_periods {where} ORDER BY period_number ASC;")

    def get_bell_period(self, period_number: int) -> Optional[Dict[str, Any]]:
        return self.db.fetchone("SELECT * FROM bell_periods WHERE period_number = ?;", (period_number,))

    def upsert_bell_period(self, period_number: int, title: str, start_time: str, end_time: str) -> Dict[str, Any]:
        # بررسی عدم تداخل زمانی زنگ‌ها با یکدیگر
        others = self.db.fetchall(
            "SELECT * FROM bell_periods WHERE period_number != ? AND is_active = 1;",
            (period_number,),
        )
        for p in others:
            if not (end_time <= p["start_time"] or start_time >= p["end_time"]):
                raise ValueError(
                    f"بازه زمانی با {p['title']} ({p['start_time']} تا {p['end_time']}) تداخل دارد."
                )
        self.db.execute(
            """
            INSERT INTO bell_periods(period_number, title, start_time, end_time, is_active)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(period_number) DO UPDATE SET
                title = excluded.title,
                start_time = excluded.start_time,
                end_time = excluded.end_time,
                is_active = 1;
            """,
            (period_number, title.strip(), start_time, end_time),
        )
        return self.get_bell_period(period_number)

    def find_class_slot_conflicts(
        self,
        class_id: int,
        day_of_week: int,
        period_number: int,
        week_type: str,
    ) -> List[Dict[str, Any]]:
        overlaps = overlapping_week_types(week_type)
        placeholders = ",".join("?" for _ in overlaps)
        return self.db.fetchall(
            f"""
            SELECT te.*, s.name AS subject_name, u.full_name AS teacher_name, c.name AS class_name
            FROM timetable_entries te
            JOIN subjects s ON s.id = te.subject_id
            JOIN classes c ON c.id = te.class_id
            LEFT JOIN users u ON u.id = te.teacher_user_id
            WHERE te.class_id = ? AND te.day_of_week = ? AND te.period_number = ?
              AND te.week_type IN ({placeholders});
            """,
            [class_id, day_of_week, period_number, *overlaps],
        )

    def find_teacher_conflicts(
        self,
        teacher_user_id: int,
        day_of_week: int,
        period_number: int,
        week_type: str,
        exclude_class_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        overlaps = overlapping_week_types(week_type)
        placeholders = ",".join("?" for _ in overlaps)
        params: List[Any] = [teacher_user_id, day_of_week, period_number, *overlaps]
        excl_sql = ""
        if exclude_class_id is not None:
            excl_sql = "AND te.class_id != ?"
            params.append(exclude_class_id)
        return self.db.fetchall(
            f"""
            SELECT te.*, s.name AS subject_name, c.name AS class_name, u.full_name AS teacher_name
            FROM timetable_entries te
            JOIN classes c ON c.id = te.class_id
            JOIN subjects s ON s.id = te.subject_id
            LEFT JOIN users u ON u.id = te.teacher_user_id
            WHERE te.teacher_user_id = ? AND te.day_of_week = ? AND te.period_number = ?
              AND te.week_type IN ({placeholders}) AND c.is_active = 1 {excl_sql};
            """,
            params,
        )

    def set_slot(
        self,
        class_id: int,
        day_of_week: int,
        period_number: int,
        week_type: str,
        subject_id: int,
        teacher_user_id: Optional[int],
        updated_by: Optional[int] = None,
        room: Optional[str] = None,
        replace_existing: bool = False,
    ) -> Dict[str, Any]:
        ts = now_iso()
        with self.db.transaction() as conn:
            if teacher_user_id is not None:
                t_conflicts = self.find_teacher_conflicts(
                    teacher_user_id, day_of_week, period_number, week_type, exclude_class_id=class_id
                )
                if t_conflicts:
                    c_item = t_conflicts[0]
                    raise ValueError(
                        f"تعارض برنامه دبیر: «{c_item['teacher_name']}» در همین روز و زنگ در کلاس «{c_item['class_name']}» برنامه دارد."
                    )

            existing = self.find_class_slot_conflicts(class_id, day_of_week, period_number, week_type)
            if existing and not replace_existing:
                # اگر دقیقاً همان week_type نیست یا اجازه جایگزینی داده نشده، خطای تعارض برگردان
                non_exact = [e for e in existing if e["week_type"] != week_type or e["subject_id"] != subject_id]
                if non_exact:
                    e0 = non_exact[0]
                    raise ValueError(
                        f"تعارض برنامه کلاس: در این زنگ قبلاً درس «{e0['subject_name']}» ({e0['week_type']}) ثبت شده است."
                    )

            old_desc = None
            if existing:
                old_desc = "; ".join(f"{e['subject_name']}({e['week_type']})" for e in existing)
                overlaps = overlapping_week_types(week_type)
                placeholders = ",".join("?" for _ in overlaps)
                conn.execute(
                    f"""
                    DELETE FROM timetable_entries
                    WHERE class_id = ? AND day_of_week = ? AND period_number = ?
                      AND week_type IN ({placeholders});
                    """,
                    [class_id, day_of_week, period_number, *overlaps],
                )

            cur = conn.execute(
                """
                INSERT INTO timetable_entries(
                    class_id, day_of_week, period_number, week_type,
                    subject_id, teacher_user_id, room, updated_by, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (class_id, day_of_week, period_number, week_type, subject_id, teacher_user_id, room, updated_by, ts),
            )
            entry_id = int(cur.lastrowid)
            subj = conn.execute("SELECT name FROM subjects WHERE id = ?;", (subject_id,)).fetchone()
            new_desc = f"{subj['name'] if subj else subject_id}({week_type})"
            conn.execute(
                """
                INSERT INTO timetable_history(
                    class_id, day_of_week, period_number, week_type,
                    action, old_value, new_value, changed_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    class_id,
                    day_of_week,
                    period_number,
                    week_type,
                    "UPDATE_SLOT" if old_desc else "SET_SLOT",
                    old_desc,
                    new_desc,
                    updated_by,
                    ts,
                ),
            )

        return self.db.fetchone(
            """
            SELECT te.*, s.name AS subject_name, u.full_name AS teacher_name, c.name AS class_name
            FROM timetable_entries te
            JOIN subjects s ON s.id = te.subject_id
            JOIN classes c ON c.id = te.class_id
            LEFT JOIN users u ON u.id = te.teacher_user_id
            WHERE te.id = ?;
            """,
            (entry_id,),
        )

    def clear_slot(
        self,
        class_id: int,
        day_of_week: int,
        period_number: int,
        week_type: Optional[str] = None,
        changed_by: Optional[int] = None,
    ) -> int:
        ts = now_iso()
        with self.db.transaction() as conn:
            if week_type and week_type.upper() != "ALL":
                overlaps = overlapping_week_types(week_type) if week_type.upper() == "BOTH" else (week_type.upper(),)
                placeholders = ",".join("?" for _ in overlaps)
                rows = conn.execute(
                    f"""
                    SELECT te.*, s.name AS subject_name
                    FROM timetable_entries te JOIN subjects s ON s.id = te.subject_id
                    WHERE te.class_id = ? AND te.day_of_week = ? AND te.period_number = ?
                      AND te.week_type IN ({placeholders});
                    """,
                    [class_id, day_of_week, period_number, *overlaps],
                ).fetchall()
                if not rows:
                    return 0
                old_desc = "; ".join(f"{r['subject_name']}({r['week_type']})" for r in rows)
                cur = conn.execute(
                    f"""
                    DELETE FROM timetable_entries
                    WHERE class_id = ? AND day_of_week = ? AND period_number = ?
                      AND week_type IN ({placeholders});
                    """,
                    [class_id, day_of_week, period_number, *overlaps],
                )
                deleted = int(cur.rowcount)
            else:
                rows = conn.execute(
                    """
                    SELECT te.*, s.name AS subject_name
                    FROM timetable_entries te JOIN subjects s ON s.id = te.subject_id
                    WHERE te.class_id = ? AND te.day_of_week = ? AND te.period_number = ?;
                    """,
                    (class_id, day_of_week, period_number),
                ).fetchall()
                if not rows:
                    return 0
                old_desc = "; ".join(f"{r['subject_name']}({r['week_type']})" for r in rows)
                cur = conn.execute(
                    "DELETE FROM timetable_entries WHERE class_id = ? AND day_of_week = ? AND period_number = ?;",
                    (class_id, day_of_week, period_number),
                )
                deleted = int(cur.rowcount)

            conn.execute(
                """
                INSERT INTO timetable_history(
                    class_id, day_of_week, period_number, week_type,
                    action, old_value, new_value, changed_by, created_at
                ) VALUES (?, ?, ?, ?, 'CLEAR_SLOT', ?, NULL, ?, ?);
                """,
                (class_id, day_of_week, period_number, week_type or "ALL", old_desc, changed_by, ts),
            )
            return deleted

    def get_class_timetable(
        self,
        class_id: int,
        week_type: Optional[str] = None,
        day_of_week: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        clauses = ["te.class_id = ?"]
        params: List[Any] = [class_id]
        if week_type in {"WEEK_1", "WEEK_2"}:
            clauses.append("te.week_type IN ('BOTH', ?)")
            params.append(week_type)
        elif week_type == "BOTH":
            clauses.append("te.week_type = 'BOTH'")
        if day_of_week is not None:
            clauses.append("te.day_of_week = ?")
            params.append(day_of_week)
        return self.db.fetchall(
            f"""
            SELECT te.*, s.name AS subject_name, u.full_name AS teacher_name,
                   c.name AS class_name, bp.start_time, bp.end_time, bp.title AS period_title
            FROM timetable_entries te
            JOIN subjects s ON s.id = te.subject_id
            JOIN classes c ON c.id = te.class_id
            JOIN bell_periods bp ON bp.period_number = te.period_number
            LEFT JOIN users u ON u.id = te.teacher_user_id
            WHERE {' AND '.join(clauses)}
            ORDER BY te.day_of_week ASC, te.period_number ASC, te.week_type ASC;
            """,
            params,
        )

    def get_teacher_timetable(
        self,
        teacher_user_id: int,
        week_type: Optional[str] = None,
        day_of_week: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        clauses = ["te.teacher_user_id = ?", "c.is_active = 1"]
        params: List[Any] = [teacher_user_id]
        if week_type in {"WEEK_1", "WEEK_2"}:
            clauses.append("te.week_type IN ('BOTH', ?)")
            params.append(week_type)
        if day_of_week is not None:
            clauses.append("te.day_of_week = ?")
            params.append(day_of_week)
        return self.db.fetchall(
            f"""
            SELECT te.*, s.name AS subject_name, c.name AS class_name, c.grade_level,
                   bp.start_time, bp.end_time, bp.title AS period_title
            FROM timetable_entries te
            JOIN classes c ON c.id = te.class_id
            JOIN subjects s ON s.id = te.subject_id
            JOIN bell_periods bp ON bp.period_number = te.period_number
            WHERE {' AND '.join(clauses)}
            ORDER BY te.day_of_week ASC, te.period_number ASC, te.week_type ASC;
            """,
            params,
        )

    def detect_all_conflicts(self) -> List[Dict[str, Any]]:
        conflicts: List[Dict[str, Any]] = []
        # تعارض احتمالی دبیر در دو کلاس هم‌زمان
        teacher_dups = self.db.fetchall(
            """
            SELECT a.id AS id1, b.id AS id2, a.day_of_week, a.period_number,
                   a.week_type AS wt1, b.week_type AS wt2,
                   u.full_name AS teacher_name, c1.name AS class1, c2.name AS class2
            FROM timetable_entries a
            JOIN timetable_entries b
              ON a.teacher_user_id = b.teacher_user_id
             AND a.id < b.id
             AND a.day_of_week = b.day_of_week
             AND a.period_number = b.period_number
            JOIN users u ON u.id = a.teacher_user_id
            JOIN classes c1 ON c1.id = a.class_id
            JOIN classes c2 ON c2.id = b.class_id
            WHERE a.teacher_user_id IS NOT NULL
              AND c1.is_active = 1 AND c2.is_active = 1
              AND (a.week_type = 'BOTH' OR b.week_type = 'BOTH' OR a.week_type = b.week_type);
            """
        )
        for row in teacher_dups:
            conflicts.append({"type": "TEACHER_CONFLICT", **row})

        class_dups = self.db.fetchall(
            """
            SELECT a.id AS id1, b.id AS id2, a.day_of_week, a.period_number,
                   a.week_type AS wt1, b.week_type AS wt2, c.name AS class_name
            FROM timetable_entries a
            JOIN timetable_entries b
              ON a.class_id = b.class_id
             AND a.id < b.id
             AND a.day_of_week = b.day_of_week
             AND a.period_number = b.period_number
            JOIN classes c ON c.id = a.class_id
            WHERE (a.week_type = 'BOTH' OR b.week_type = 'BOTH' OR a.week_type = b.week_type);
            """
        )
        for row in class_dups:
            conflicts.append({"type": "CLASS_CONFLICT", **row})
        return conflicts

    def list_history(self, class_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
        if class_id is not None:
            return self.db.fetchall(
                """
                SELECT th.*, c.name AS class_name, u.full_name AS actor_name
                FROM timetable_history th
                LEFT JOIN classes c ON c.id = th.class_id
                LEFT JOIN users u ON u.id = th.changed_by
                WHERE th.class_id = ?
                ORDER BY th.id DESC LIMIT ?;
                """,
                (class_id, limit),
            )
        return self.db.fetchall(
            """
            SELECT th.*, c.name AS class_name, u.full_name AS actor_name
            FROM timetable_history th
            LEFT JOIN classes c ON c.id = th.class_id
            LEFT JOIN users u ON u.id = th.changed_by
            ORDER BY th.id DESC LIMIT ?;
            """,
            (limit,),
        )
