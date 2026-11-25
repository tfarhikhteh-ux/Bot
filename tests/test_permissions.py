from __future__ import annotations

import pytest

from config import AppConfig, ConfigurationError
from services import AccessDeniedError


def test_missing_primary_admin_halts_safely(tmp_path):
    cfg = AppConfig.from_mapping({"BALE_BOT_TOKEN": "123456:ABCDEF12345678901234"}, root_dir=tmp_path)
    with pytest.raises(ConfigurationError):
        cfg.validate_primary_admin()


def test_admin_exclusive_vs_school_manager_restrictions(app_env, actors):
    svc = app_env["services"]
    admin = actors["admin"]
    manager = actors["manager"]

    # مدیر مدرسه نمی‌تواند ادمین یا مدیر جدید ایجاد کند
    with pytest.raises(AccessDeniedError):
        svc.registration.create_user_direct(manager, 880001, "ادمین غیرمجاز", "ADMIN")
    with pytest.raises(AccessDeniedError):
        svc.registration.create_user_direct(manager, 880002, "مدیر غیرمجاز", "SCHOOL_MANAGER")

    # مدیر مدرسه نمی‌تواند ادمین را غیرفعال یا تغییر نقش دهد
    with pytest.raises(AccessDeniedError):
        svc.registration.change_user_role(manager, admin["id"], "TEACHER")
    with pytest.raises(AccessDeniedError):
        svc.registration.set_user_active(manager, admin["id"], False)

    # مدیر مدرسه نمی‌تواند تنظیمات امنیتی اختصاصی ادمین را تغییر دهد
    with pytest.raises(AccessDeniedError):
        svc.notifications.update_setting(manager, "strict_holiday_attendance_lock", "0")

    # ادمین مجاز به تغییر تنظیم امنیتی است
    svc.notifications.update_setting(admin, "strict_holiday_attendance_lock", "1")


def test_self_role_escalation_blocked(app_env, actors):
    svc = app_env["services"]
    manager = actors["manager"]
    teacher = actors["teacher1"]
    with pytest.raises(AccessDeniedError):
        svc.registration.change_user_role(manager, manager["id"], "ADMIN")
    with pytest.raises(AccessDeniedError):
        svc.registration.change_user_role(teacher, teacher["id"], "SCHOOL_MANAGER")


def test_teacher_student_parent_data_boundaries(app_env, actors):
    svc = app_env["services"]
    mgr = actors["manager"]
    t1, t2 = actors["teacher1"], actors["teacher2"]
    s1, s2 = actors["student1"], actors["student2"]
    p1 = actors["parent1"]

    cls1 = svc.timetable.create_class(mgr, "دهم-۱", "10")
    cls2 = svc.timetable.create_class(mgr, "دهم-۲", "10")
    subj = svc.timetable.create_subject(mgr, "شیمی", "10", "تخصصی")
    svc.timetable.assign_subject_to_class(mgr, cls1["id"], subj["id"], t1["id"])
    svc.timetable.enroll_student_in_class(mgr, cls1["id"], s1["id"])
    svc.timetable.enroll_student_in_class(mgr, cls2["id"], s2["id"])

    # دبیر اول به کلاس اول دسترسی دارد اما به کلاس دوم دسترسی ندارد
    svc.permissions.ensure_can_access_class(t1, cls1["id"])
    with pytest.raises(AccessDeniedError):
        svc.permissions.ensure_can_access_class(t1, cls2["id"])
    with pytest.raises(AccessDeniedError):
        svc.permissions.ensure_can_access_class(t2, cls1["id"])

    # دانش‌آموز اول فقط اطلاعات خودش را می‌بیند
    svc.permissions.ensure_can_access_student(s1, s1["id"])
    with pytest.raises(AccessDeniedError):
        svc.permissions.ensure_can_access_student(s1, s2["id"])

    # ولی اول فقط به اطلاعات فرزند ثبت‌شده خودش دسترسی دارد
    svc.permissions.ensure_can_access_student(p1, s1["id"])
    with pytest.raises(AccessDeniedError):
        svc.permissions.ensure_can_access_student(p1, s2["id"])


def test_deputy_and_association_member_permissions(app_env, actors):
    svc = app_env["services"]
    mgr = actors["manager"]
    deputy = actors["deputy"]
    assoc = actors["assoc"]

    assert svc.permissions.has_permission(deputy, "RECORD_DISCIPLINARY")
    assert not svc.permissions.has_permission(deputy, "BACKUP_CREATE")

    assert not svc.permissions.has_permission(assoc, "VIEW_REPORTS_ALL")
    updated_assoc = svc.registration.grant_association_permissions(mgr, assoc["id"], ["VIEW_REPORTS_ALL", "BACKUP_RESTORE"])
    # مجوز مجاز اعمال می‌شود ولی مجوز انحصاری ادمین هرگز اعطا نمی‌شود
    assert svc.permissions.has_permission(updated_assoc, "VIEW_REPORTS_ALL")
    assert not svc.permissions.has_permission(updated_assoc, "BACKUP_RESTORE")
