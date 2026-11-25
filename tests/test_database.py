from __future__ import annotations

import sqlite3
import pytest

from database.migrations import run_migrations


def test_database_tables_and_integrity(app_env):
    db = app_env["db"]
    assert db.check_integrity() is True
    tables = {
        r["name"]
        for r in db.fetchall("SELECT name FROM sqlite_master WHERE type='table';")
    }
    expected = {
        "schema_migrations",
        "users",
        "parent_students",
        "registration_requests",
        "classes",
        "class_enrollments",
        "class_history",
        "subjects",
        "class_subjects",
        "bell_periods",
        "timetable_entries",
        "timetable_history",
        "attendance_records",
        "attendance_history",
        "disciplinary_notes",
        "evaluation_types",
        "grades",
        "grade_history",
        "homework_items",
        "announcements",
        "message_queue",
        "holidays",
        "settings",
        "backups",
        "scheduled_tasks",
        "user_sessions",
        "audit_logs",
        "continuous_modifiers",
    }
    assert expected.issubset(tables)


def test_migrations_are_idempotent(app_env):
    db = app_env["db"]
    second_run = run_migrations(db)
    assert second_run == []
    versions = [r["version"] for r in db.fetchall("SELECT version FROM schema_migrations ORDER BY version;")]
    assert versions == [1, 2, 3, 4]


def test_foreign_key_enforced(app_env):
    db = app_env["db"]
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO parent_students(parent_user_id, student_user_id, relation, created_at) VALUES (99999, 88888, 'والد', '2026-01-01');"
        )


def test_transaction_rollback_on_error(app_env):
    db = app_env["db"]
    before_count = db.fetchone("SELECT COUNT(*) AS c FROM subjects;")["c"]
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO subjects(name, grade_level, subject_type, created_at) VALUES ('درس موقت', '10', 'عمومی', '2026-01-01');"
            )
            raise RuntimeError("شبیه‌سازی خطا در وسط تراکنش")
    after_count = db.fetchone("SELECT COUNT(*) AS c FROM subjects;")["c"]
    assert after_count == before_count


def test_duplicate_bale_id_blocked(app_env):
    svc = app_env["services"]
    admin = app_env["admin"]
    svc.registration.create_user_direct(admin, 777001, "کاربر یکتا", "STUDENT")
    with pytest.raises(Exception):
        svc.registration.create_user_direct(admin, 777001, "کاربر تکراری", "STUDENT")
