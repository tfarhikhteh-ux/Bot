from __future__ import annotations

import pytest

from utils.validators import ValidationError


def test_subjects_crud_controlled_delete_and_teacher_assignments(app_env, actors):
    svc = app_env["services"]
    mgr = actors["manager"]
    t1 = actors["teacher1"]

    s_unused = svc.timetable.create_subject(mgr, "هنر", "10", "عمومی", code="ART10", default_weekly_periods=1)
    s_used = svc.timetable.create_subject(mgr, "زمین‌شناسی", "11", "تخصصی", code="GEO11", default_weekly_periods=2)

    # جلوگیری از ایجاد درس تکراری در همان پایه
    with pytest.raises(ValidationError):
        svc.timetable.create_subject(mgr, "هنر", "10", "عمومی")

    cls = svc.timetable.create_class(mgr, "یازدهم-تجربی", "11")
    svc.timetable.assign_subject_to_class(mgr, cls["id"], s_used["id"], t1["id"], weekly_periods=2, coefficient=2.0)

    # بررسی گزارش دروس کلاس و دروس دبیر
    class_subjs = svc.timetable.subjects.list_class_subjects(cls["id"])
    teacher_subjs = svc.timetable.subjects.list_teacher_subjects(t1["id"])
    assert len(class_subjs) == 1 and class_subjs[0]["subject_name"] == "زمین‌شناسی"
    assert len(teacher_subjs) == 1 and teacher_subjs[0]["class_name"] == "یازدهم-تجربی"

    # حذف کنترل‌شده: درس بدون استفاده حذف کامل و درس تخصیص‌یافته بایگانی می‌شود
    assert svc.timetable.delete_subject_controlled(mgr, s_unused["id"]) == "DELETED"
    assert svc.timetable.subjects.get_by_id(s_unused["id"]) is None

    assert svc.timetable.delete_subject_controlled(mgr, s_used["id"]) == "ARCHIVED"
    assert svc.timetable.subjects.get_by_id(s_used["id"])["is_active"] == 0
