from __future__ import annotations

import pytest

from utils.validators import ValidationError


def test_timetable_alternating_weeks_conflicts_and_exports(app_env, actors):
    svc = app_env["services"]
    mgr = actors["manager"]
    t1, t2 = actors["teacher1"], actors["teacher2"]
    s1 = actors["student1"]

    c1 = svc.timetable.create_class(mgr, "دهم-الف", "10")
    c2 = svc.timetable.create_class(mgr, "دهم-ب", "10")
    svc.timetable.enroll_student_in_class(mgr, c1["id"], s1["id"])

    math = svc.timetable.create_subject(mgr, "ریاضی", "10", "تخصصی")
    phys = svc.timetable.create_subject(mgr, "فیزیک", "10", "تخصصی")
    lab = svc.timetable.create_subject(mgr, "آزمایشگاه", "10", "آزمایشگاه")

    svc.timetable.assign_subject_to_class(mgr, c1["id"], math["id"], t1["id"])
    svc.timetable.assign_subject_to_class(mgr, c1["id"], phys["id"], t2["id"])
    svc.timetable.assign_subject_to_class(mgr, c1["id"], lab["id"], t2["id"])
    svc.timetable.assign_subject_to_class(mgr, c2["id"], math["id"], t1["id"])

    # درس مشترک هر دو هفته در زنگ ۱ شنبه
    svc.timetable.set_timetable_slot(mgr, c1["id"], "شنبه", 1, "BOTH", math["id"])

    # هفته اول فیزیک و هفته دوم آزمایشگاه در زنگ ۲ شنبه (بدون تعارض چون هفته‌ها متناوب هستند)
    svc.timetable.set_timetable_slot(mgr, c1["id"], "شنبه", 2, "WEEK_1", phys["id"])
    svc.timetable.set_timetable_slot(mgr, c1["id"], "شنبه", 2, "WEEK_2", lab["id"])

    w1_entries = svc.timetable.timetable.get_class_timetable(c1["id"], week_type="WEEK_1")
    w2_entries = svc.timetable.timetable.get_class_timetable(c1["id"], week_type="WEEK_2")
    assert {e["subject_name"] for e in w1_entries} == {"ریاضی", "فیزیک"}
    assert {e["subject_name"] for e in w2_entries} == {"ریاضی", "آزمایشگاه"}

    # جلوگیری از برنامه هم‌زمان یک دبیر در دو کلاس (t1 در شنبه زنگ ۱ در کلاس دهم-الف حضور دارد)
    with pytest.raises(ValidationError):
        svc.timetable.set_timetable_slot(mgr, c2["id"], "شنبه", 1, "WEEK_1", math["id"], teacher_user_id=t1["id"])

    # جلوگیری از دو درس متفاوت در یک زنگ کلاس بدون اجازه جایگزینی
    with pytest.raises(ValidationError):
        svc.timetable.set_timetable_slot(mgr, c1["id"], "شنبه", 1, "WEEK_1", phys["id"], replace_existing=False)

    # دستور سریع ویرایش و پاک‌کردن خانه خالی
    msg = svc.timetable.apply_quick_edit_command(mgr, "دهم-الف | یکشنبه | 3 | هفته اول | فیزیک")
    assert "به‌روزرسانی شد" in msg
    msg_clear = svc.timetable.apply_quick_edit_command(mgr, "دهم-الف | یکشنبه | 3 | هفته اول | حذف")
    assert "پاک شد" in msg_clear

    # بررسی برنامه شخصی دبیر و خروجی PNG / PDF
    t_text = svc.timetable.get_formatted_teacher_timetable(t1)
    assert "ریاضی" in t_text and "دهم-الف" in t_text

    png = svc.timetable.export_class_timetable_png(s1, c1["id"], week_type="WEEK_1")
    pdf = svc.timetable.export_class_timetable_pdf(s1, c1["id"], week_type="WEEK_2")
    assert png.startswith(b"\x89PNG")
    assert pdf.startswith(b"%PDF")
    assert svc.timetable.timetable.detect_all_conflicts() == []
