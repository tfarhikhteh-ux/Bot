from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from utils.dates import now_iso

from .db import DatabaseManager

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _migration_001_initial_schema(conn: sqlite3.Connection) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.execute("PRAGMA foreign_keys = ON;")


def _migration_002_seed_defaults(conn: sqlite3.Connection) -> None:
    ts = now_iso()
    default_settings = [
        ("school_name", "دبیرستان نمونه", "GENERAL", 0),
        ("academic_year", "1405-1406", "CALENDAR", 0),
        ("current_term", "ترم اول", "CALENDAR", 0),
        ("term1_start", "2026-09-23", "CALENDAR", 0),
        ("term1_end", "2027-01-20", "CALENDAR", 0),
        ("term2_start", "2027-01-21", "CALENDAR", 0),
        ("term2_end", "2027-06-20", "CALENDAR", 0),
        ("teaching_days", json.dumps([0, 1, 2, 3, 4]), "CALENDAR", 0),
        ("active_week_mode", "AUTO", "TIMETABLE", 0),
        ("homework_enabled", "1", "ACADEMIC", 0),
        ("notify_parents_attendance", "1", "NOTIFICATIONS", 0),
        ("notify_parents_grades", "1", "NOTIFICATIONS", 0),
        ("registration_open", "1", "SECURITY", 0),
        ("attendance_reminder_hour", "09:30", "SCHEDULER", 0),
        ("grade_reminder_day", "4", "SCHEDULER", 0),
        ("management_group_id", "", "SECURITY", 1),
        ("backup_retention_count", "10", "SECURITY", 1),
        ("auto_backup_enabled", "1", "SECURITY", 1),
        ("strict_holiday_attendance_lock", "1", "SECURITY", 1),
    ]
    for key, val, cat, admin_only in default_settings:
        conn.execute(
            """
            INSERT OR IGNORE INTO settings(key, value, category, admin_only, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (key, val, cat, admin_only, ts),
        )

    default_bells = [
        (1, "زنگ اول", "07:45", "09:00"),
        (2, "زنگ دوم", "09:15", "10:30"),
        (3, "زنگ سوم", "10:45", "12:00"),
        (4, "زنگ چهارم", "12:30", "13:45"),
        (5, "زنگ پنجم", "14:00", "15:15"),
    ]
    for num, title, start_t, end_t in default_bells:
        conn.execute(
            """
            INSERT OR IGNORE INTO bell_periods(period_number, title, start_time, end_time, is_active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (num, title, start_t, end_t),
        )

    default_evals = [
        ("ارزشیابی مستمر نوبت اول", "ترم اول", 0.0, 20.0, 1.0),
        ("آزمون میان‌ترم اول", "ترم اول", 0.0, 20.0, 1.0),
        ("آزمون پایان‌ترم اول", "ترم اول", 0.0, 20.0, 2.0),
        ("ارزشیابی مستمر نوبت دوم", "ترم دوم", 0.0, 20.0, 1.0),
        ("آزمون پایان‌ترم دوم", "ترم دوم", 0.0, 20.0, 4.0),
    ]
    for name, term, min_s, max_s, weight in default_evals:
        conn.execute(
            """
            INSERT OR IGNORE INTO evaluation_types(name, term, min_score, max_score, weight, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (name, term, min_s, max_s, weight),
        )


def _migration_003_reporting_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_disciplinary_student ON disciplinary_notes(student_user_id, note_date);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_homework_class ON homework_items(class_id, is_active, due_date);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_grade_history_grade ON grade_history(grade_id, created_at);")


def _migration_004_continuous_modifiers_and_portal(conn: sqlite3.Connection) -> None:
    ts = now_iso()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS continuous_modifiers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
            subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
            teacher_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            academic_term TEXT NOT NULL,
            delta REAL NOT NULL CHECK (delta != 0),
            old_score REAL NOT NULL,
            new_score REAL NOT NULL,
            note TEXT,
            source_chat_id INTEGER,
            source_message_id INTEGER,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_continuous_mod_unique_msg
            ON continuous_modifiers(source_chat_id, source_message_id)
            WHERE source_chat_id IS NOT NULL AND source_message_id IS NOT NULL;
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_continuous_mod_student
            ON continuous_modifiers(student_user_id, academic_term, subject_id);
        """
    )
    portal_settings = [
        ("continuous_base_score", "15", "ACADEMIC", 0),
        ("online_report_card_enabled", "1", "ACADEMIC", 0),
        ("public_web_url", "http://127.0.0.1:5000", "GENERAL", 0),
    ]
    for key, val, cat, admin_only in portal_settings:
        conn.execute(
            """
            INSERT OR IGNORE INTO settings(key, value, category, admin_only, updated_at)
            VALUES (?, ?, ?, ?, ?);
            """,
            (key, val, cat, admin_only, ts),
        )


MIGRATIONS: List[Tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "001_initial_schema", _migration_001_initial_schema),
    (2, "002_seed_defaults", _migration_002_seed_defaults),
    (3, "003_reporting_indexes", _migration_003_reporting_indexes),
    (4, "004_continuous_modifiers_and_portal", _migration_004_continuous_modifiers_and_portal),
]


def run_migrations(
    db: DatabaseManager,
    school_name: Optional[str] = None,
    academic_year: Optional[str] = None,
    current_term: Optional[str] = None,
    management_group_id: Optional[int] = None,
) -> List[str]:
    applied_names: List[str] = []
    conn = db.get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        """
    )

    existing_versions = {
        row["version"] for row in db.fetchall("SELECT version FROM schema_migrations ORDER BY version ASC;")
    }

    for version, name, migrate_fn in MIGRATIONS:
        if version in existing_versions:
            continue
        if version == 1:
            migrate_fn(conn)
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?);",
                    (version, name, now_iso()),
                )
        else:
            with db.transaction() as tx:
                migrate_fn(tx)
                tx.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?);",
                    (version, name, now_iso()),
                )
        applied_names.append(name)

    ts = now_iso()
    with db.transaction() as tx:
        if school_name:
            tx.execute(
                "UPDATE settings SET value = ?, updated_at = ? WHERE key = 'school_name' AND updated_by IS NULL;",
                (school_name, ts),
            )
        if academic_year:
            tx.execute(
                "UPDATE settings SET value = ?, updated_at = ? WHERE key = 'academic_year' AND updated_by IS NULL;",
                (academic_year, ts),
            )
        if current_term:
            tx.execute(
                "UPDATE settings SET value = ?, updated_at = ? WHERE key = 'current_term' AND updated_by IS NULL;",
                (current_term, ts),
            )
        if management_group_id is not None:
            tx.execute(
                "UPDATE settings SET value = ?, updated_at = ? WHERE key = 'management_group_id' AND value = '';",
                (str(management_group_id), ts),
            )

    return applied_names


def ensure_primary_admin(db: DatabaseManager, primary_admin_bale_id: int, full_name: str = "مدیر ارشد سامانه") -> dict:
    if not primary_admin_bale_id or primary_admin_bale_id <= 0:
        raise ValueError("شناسه ادمین اصلی باید عددی مثبت باشد.")
    ts = now_iso()
    existing = db.fetchone("SELECT * FROM users WHERE bale_user_id = ?;", (primary_admin_bale_id,))
    if existing is None:
        with db.transaction() as tx:
            tx.execute(
                """
                INSERT INTO users(bale_user_id, full_name, role, is_active, created_at, updated_at)
                VALUES (?, ?, 'ADMIN', 1, ?, ?);
                """,
                (primary_admin_bale_id, full_name, ts, ts),
            )
    elif existing["role"] != "ADMIN" or not existing["is_active"]:
        with db.transaction() as tx:
            tx.execute(
                "UPDATE users SET role = 'ADMIN', is_active = 1, updated_at = ? WHERE id = ?;",
                (ts, existing["id"]),
            )
    return db.fetchone("SELECT * FROM users WHERE bale_user_id = ?;", (primary_admin_bale_id,))
