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


def test_positive_and_negative_continuous_modifiers_never_touch_final_exam(app_env, actors):
    from services.grade_service import AmbiguousTeacherSubjectError

    svc = app_env["services"]
    mgr = actors["manager"]
    t1, t2 = actors["teacher1"], actors["teacher2"]
    s1 = actors["student1"]

    cls = svc.timetable.create_class(mgr, "دهم-مستمر", "10")
    phys = svc.timetable.create_subject(mgr, "فیزیک ۱", "10", "تخصصی", default_coefficient=3.0)
    svc.timetable.assign_subject_to_class(mgr, cls["id"], phys["id"], t1["id"], coefficient=3.0)
    svc.timetable.enroll_student_in_class(mgr, cls["id"], s1["id"])

    evals = {e["name"]: e for e in svc.grades.grades.list_evaluation_types()}
    cont_eval = evals["ارزشیابی مستمر نوبت اول"]
    final_eval = evals["آزمون پایان‌ترم اول"]

    # ثبت نمره مستمر اولیه 17 و نمره پایانی 18.5
    svc.grades.record_grade(t1, s1["id"], cls["id"], phys["id"], cont_eval["id"], 17.0)
    final_row = svc.grades.record_grade(t1, s1["id"], cls["id"], phys["id"], final_eval["id"], 18.5)

    # ۱. مثبت درسی توسط دبیر همان درس -> مستمر می‌شود 18، پایانی دست‌نخورده (18.5) باقی می‌ماند
    r_pos = svc.grades.apply_continuous_modifier(t1, s1["id"], delta=1.0, source_chat_id=-10055, source_message_id=101)
    assert r_pos["old_score"] == 17.0
    assert r_pos["new_score"] == 18.0
    assert r_pos["positive_count"] == 1 and r_pos["negative_count"] == 0
    assert svc.grades.grades.get_by_id(final_row["id"])["score"] == 18.5

    # ۲. منفی درسی توسط دبیر -> مستمر از 18 می‌شود 17، پایانی همچنان دقیقاً 18.5 است
    r_neg = svc.grades.apply_continuous_modifier(t1, s1["id"], delta=-1.0, source_chat_id=-10055, source_message_id=102)
    assert r_neg["old_score"] == 18.0
    assert r_neg["new_score"] == 17.0
    assert r_neg["positive_count"] == 1 and r_neg["negative_count"] == 1
    assert svc.grades.grades.get_by_id(final_row["id"])["score"] == 18.5

    # ۳. دانش‌آموز یا دبیر غیرمرتبط با کلاس اجازه ثبت مثبت/منفی درسی ندارد
    with pytest.raises(AccessDeniedError):
        svc.grades.apply_continuous_modifier(s1, s1["id"], delta=1.0)
    with pytest.raises(AccessDeniedError):
        svc.grades.apply_continuous_modifier(t2, s1["id"], delta=1.0)

    # ۴. اگر دبیر دو درس در همان کلاس داشته باشد بدون نام بردن درس، خطای ابهام درس می‌دهد تا دکمه نمایش داده شود
    chem = svc.timetable.create_subject(mgr, "شیمی ۱", "10", "تخصصی", default_coefficient=3.0)
    svc.timetable.assign_subject_to_class(mgr, cls["id"], chem["id"], t1["id"], coefficient=3.0)
    with pytest.raises(AmbiguousTeacherSubjectError):
        svc.grades.apply_continuous_modifier(t1, s1["id"], delta=1.0)

    # با مشخص کردن نام درس، دقیقاً روی مستمر شیمی اعمال می‌شود (پایه مستمر پیش‌فرض 15 + 1 = 16)
    r_chem = svc.grades.apply_continuous_modifier(t1, s1["id"], delta=1.0, subject_hint="شیمی ۱")
    assert r_chem["subject_name"] == "شیمی ۱" and r_chem["new_score"] == 16.0

