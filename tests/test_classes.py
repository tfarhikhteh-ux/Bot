from __future__ import annotations

import pytest

from utils.validators import ValidationError


def test_classes_subjects_enroll_transfer_and_controlled_delete(app_env, actors):
    svc = app_env["services"]
    mgr = actors["manager"]
    t1 = actors["teacher1"]
    s1, s2 = actors["student1"], actors["student2"]

    c_empty = svc.timetable.create_class(mgr, "نهم-خالی", "9", "1405-1406")
    c1 = svc.timetable.create_class(mgr, "دهم-تجربی", "10", "1405-1406", capacity=30)
    c2 = svc.timetable.create_class(mgr, "دهم-ریاضی", "10", "1405-1406", capacity=30)

    # ویرایش کلاس
    c1 = svc.timetable.update_class(mgr, c1["id"], capacity=32)
    assert c1["capacity"] == 32

    # ثبت دانش‌آموز در کلاس و جلوگیری از ثبت هم‌زمان در دو کلاس در یک سال تحصیلی
    svc.timetable.enroll_student_in_class(mgr, c1["id"], s1["id"])
    svc.timetable.enroll_student_in_class(mgr, c1["id"], s2["id"])
    with pytest.raises(ValidationError):
        svc.timetable.enroll_student_in_class(mgr, c2["id"], s1["id"])

    # انتقال دانش‌آموز بین کلاس‌ها و ثبت تاریخچه
    svc.timetable.transfer_student_between_classes(mgr, s1["id"], c2["id"], note="تغییر رشته")
    assert svc.timetable.classes.get_student_active_class(s1["id"])["id"] == c2["id"]
    hist = svc.timetable.classes.get_history(student_user_id=s1["id"])
    assert any(h["action"] == "TRANSFER_STUDENT" for h in hist)

    # دانش‌آموز غیرفعال در فهرست فعال کلاس قرار نمی‌گیرد
    svc.registration.set_user_active(mgr, s2["id"], False)
    active_list = svc.timetable.classes.list_class_students(c1["id"], active_users_only=True)
    assert all(st["id"] != s2["id"] for st in active_list)

    # حذف کلاس بدون سابقه => DELETED، حذف کلاس دارای سابقه => ARCHIVED
    del_empty = svc.timetable.delete_class_controlled(mgr, c_empty["id"])
    assert del_empty["mode"] == "DELETED"
    del_used = svc.timetable.delete_class_controlled(mgr, c1["id"])
    assert del_used["mode"] == "ARCHIVED"
    assert svc.timetable.classes.get_by_id(c1["id"])["is_active"] == 0

    # تعریف درس و تخصیص دبیر و جلوگیری از تخصیص تکراری
    subj = svc.timetable.create_subject(mgr, "زیست‌شناسی", "10", "تخصصی", default_weekly_periods=3, default_coefficient=4.0)
    svc.timetable.assign_subject_to_class(mgr, c2["id"], subj["id"], t1["id"], weekly_periods=3, coefficient=4.0)
    with pytest.raises(ValidationError):
        svc.timetable.assign_subject_to_class(mgr, c2["id"], subj["id"], t1["id"])
