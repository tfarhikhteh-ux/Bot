from __future__ import annotations

import pytest

from services import AccessDeniedError
from utils.validators import ValidationError


def test_grades_validation_gpa_update_delete_and_access(app_env, actors):
    svc = app_env["services"]
    mgr = actors["manager"]
    t1, t2 = actors["teacher1"], actors["teacher2"]
    s1, s2 = actors["student1"], actors["student2"]
    p1 = actors["parent1"]

    cls = svc.timetable.create_class(mgr, "دهم-نمرات", "10")
    math = svc.timetable.create_subject(mgr, "حسابان", "10", "تخصصی", default_coefficient=4.0)
    lit = svc.timetable.create_subject(mgr, "فارسی", "10", "عمومی", default_coefficient=2.0)

    svc.timetable.assign_subject_to_class(mgr, cls["id"], math["id"], t1["id"], coefficient=4.0)
    svc.timetable.assign_subject_to_class(mgr, cls["id"], lit["id"], t2["id"], coefficient=2.0)
    svc.timetable.enroll_student_in_class(mgr, cls["id"], s1["id"])
    svc.timetable.enroll_student_in_class(mgr, cls["id"], s2["id"])

    ev = svc.grades.create_evaluation_type(mgr, "آزمون جامع اول", "ترم اول", min_score=0.0, max_score=20.0, weight=2.0)

    # اعتبارسنجی بازه نمره
    with pytest.raises(ValidationError):
        svc.grades.record_grade(t1, s1["id"], cls["id"], math["id"], ev["id"], 21.5)

    # دبیر اول اجازه ثبت نمره درس فارسی (متعلق به دبیر دوم) را ندارد
    with pytest.raises(AccessDeniedError):
        svc.grades.record_grade(t1, s1["id"], cls["id"], lit["id"], ev["id"], 18.0)

    # ثبت نمره معتبر: حسابان (ضریب ۴) = ۱۹ ، فارسی (ضریب ۲) = ۱۶ => معدل وزنی = (19*4 + 16*2)/6 = 108/6 = 18.00
    g_math = svc.grades.record_grade(t1, s1["id"], cls["id"], math["id"], ev["id"], 19.0)
    g_lit = svc.grades.record_grade(t2, s1["id"], cls["id"], lit["id"], ev["id"], 16.0)

    rc = svc.grades.get_student_report_card(s1, s1["id"])
    assert rc["gpa"] == 18.00

    # ولی اول کارنامه فرزندش را می‌بیند ولی دانش‌آموز دوم اجازه دیدن کارنامه دانش‌آموز اول را ندارد
    assert svc.grades.get_student_report_card(p1, s1["id"])["gpa"] == 18.00
    with pytest.raises(AccessDeniedError):
        svc.grades.get_student_report_card(s2, s1["id"])

    # ویرایش نمره و بررسی تاریخچه تغییر و تغییر معدل
    svc.grades.update_grade(t1, g_math["id"], 20.0, reason="تصحیح مجدد برگه")
    rc_after = svc.grades.get_student_report_card(s1, s1["id"])
    # (20*4 + 16*2) / 6 = 112 / 6 = 18.67
    assert rc_after["gpa"] == 18.67

    hist = svc.grades.grades.list_history(grade_id=g_math["id"])
    assert len(hist) == 2 and hist[1]["old_score"] == 19.0 and hist[1]["new_score"] == 20.0

    # حذف کنترل‌شده نمره فارسی => معدل فقط بر اساس حسابان (20.0) می‌شود
    svc.grades.delete_grade_controlled(mgr, g_lit["id"], reason="ثبت اشتباه")
    assert svc.grades.get_student_report_card(s1, s1["id"])["gpa"] == 20.0
