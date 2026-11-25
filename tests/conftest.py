from __future__ import annotations

from pathlib import Path
from typing import Dict

import pytest

from config import AppConfig
from database import DatabaseManager, ensure_primary_admin, run_migrations
from services import ServiceContainer


@pytest.fixture()
def app_env(tmp_path: Path):
    env_map = {
        "BALE_BOT_TOKEN": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
        "PRIMARY_ADMIN_ID": "900001",
        "PRIMARY_ADMIN_NAME": "ادمین آزمایشی",
        "MANAGEMENT_GROUP_ID": "-100555000",
        "DATABASE_PATH": str(tmp_path / "test_school.db"),
        "BACKUP_DIR": str(tmp_path / "backups"),
        "LOG_DIR": str(tmp_path / "logs"),
        "SCHOOL_NAME": "دبیرستان آزمایشی نمونه",
        "ACADEMIC_YEAR": "1405-1406",
        "CURRENT_TERM": "ترم اول",
        "BACKUP_RETENTION_COUNT": "4",
    }
    cfg = AppConfig.from_mapping(env_map, root_dir=tmp_path)
    cfg.ensure_directories()
    db = DatabaseManager(cfg.database_path)
    run_migrations(
        db,
        school_name=cfg.school_name,
        academic_year=cfg.academic_year,
        current_term=cfg.current_term,
        management_group_id=cfg.management_group_id,
    )
    admin = ensure_primary_admin(db, cfg.primary_admin_id, cfg.primary_admin_name)
    services = ServiceContainer.build(
        db=db,
        backup_dir=cfg.backup_dir,
        primary_admin_bale_id=cfg.primary_admin_id,
        management_group_id=cfg.management_group_id,
        backup_retention_count=cfg.backup_retention_count,
    )
    yield {"config": cfg, "db": db, "services": services, "admin": admin}
    db.close_thread_connection()


@pytest.fixture()
def actors(app_env) -> Dict[str, dict]:
    svc: ServiceContainer = app_env["services"]
    admin = app_env["admin"]
    manager = svc.registration.create_user_direct(admin, 900002, "مدیر آزمایشی", "SCHOOL_MANAGER")
    deputy = svc.registration.create_user_direct(manager, 900003, "ناظم آزمایشی", "DEPUTY")
    assoc = svc.registration.create_user_direct(manager, 900004, "عضو انجمن آزمایشی", "ASSOCIATION_MEMBER")
    teacher1 = svc.registration.create_user_direct(manager, 900005, "دبیر اول", "TEACHER", phone="09120000001")
    teacher2 = svc.registration.create_user_direct(manager, 900006, "دبیر دوم", "TEACHER", phone="09120000002")
    student1 = svc.registration.create_user_direct(
        manager, 900007, "دانش‌آموز اول", "STUDENT", student_code="STU101", grade_level="10"
    )
    student2 = svc.registration.create_user_direct(
        manager, 900008, "دانش‌آموز دوم", "STUDENT", student_code="STU102", grade_level="10"
    )
    parent1 = svc.registration.create_user_direct(manager, 900009, "ولی اول", "PARENT", phone="09120000009")
    svc.registration.link_parent_student(manager, parent1["id"], student1["id"])
    return {
        "admin": admin,
        "manager": manager,
        "deputy": deputy,
        "assoc": assoc,
        "teacher1": teacher1,
        "teacher2": teacher2,
        "student1": student1,
        "student2": student2,
        "parent1": parent1,
    }
