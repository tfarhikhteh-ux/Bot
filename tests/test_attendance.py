from __future__ import annotations

import pytest

from services import AccessDeniedError
from utils.validators import ValidationError


def test_attendance_record_update_duplicate_and_reports(app_env, actors):
    svc = app_env["services"]
    mgr = actors["manager"]
    deputy = actors["deputy"]
    t1, t2 = actors["teacher1"], actors["teacher2"]
    s1, s2 = actors["student1"], actors["student2"]
    p1 = actors["parent1"]

    cls = svc.timetable.create_class(mgr, "دهم-حضور", "10")
    subj = svc.timetable.create_subject(mgr, "فیزیک", "10")
    svc.timetable.assign_subject_to_class(mgr, cls["id"], subj["id"], t1["id"])
    svc.timetable.enroll_student_in_class(mgr, cls["id"], s1["id"])
    svc.timetable.enroll_student_in_class(mgr, cls["id"], s2["id"])

    # دبیر غیرمرتبط مجاز به ثبت حضور در این کلاس نیست
    with pytest.raises(AccessDeniedError):
        svc.attendance.record_attendance(t2, cls["id"], s1["id"], "PRESENT", att_date="2026-10-10", period_number=1)

    rec1 = svc.attendance.record_attendance(t1, cls["id"], s1["id"], "ABSENT", att_date="2026-10-10", period_number=1, note="بدون اطلاع")
    rec2 = svc.attendance.record_attendance(t1, cls["id"], s2["id"], "LATE", att_date="2026-10-10", period_number=1)
    assert rec1["status"] == "ABSENT"
    assert rec2["status"] == "LATE"

    # جلوگیری از ثبت تکراری همان تاریخ و زنگ
    with pytest.raises(ValidationError):
        svc.attendance.record_attendance(t1, cls["id"], s1["id"], "PRESENT", att_date="2026-10-10", period_number=1)

    # اطلاع‌رسانی خودکار غیبت به ولی در صف پیام قرار گرفته است
    parent_alerts = svc.db.fetchall("SELECT * FROM message_queue WHERE message_type = 'PARENT_ATTENDANCE_ALERT';")
    assert any(a["recipient_chat_id"] == p1["bale_user_id"] for a in parent_alerts)

    # اصلاح وضعیت توسط ناظم با ذکر علت و ثبت تاریخچه
    updated = svc.attendance.update_attendance(deputy, rec1["id"], "EXCUSED_ABSENT", reason="ارائه گواهی پزشکی")
    assert updated["status"] == "EXCUSED_ABSENT"
    hist = svc.attendance.attendance.get_history(rec1["id"])
    assert len(hist) == 2 and hist[1]["old_status"] == "ABSENT" and hist[1]["new_status"] == "EXCUSED_ABSENT"

    # گزارش‌گیری کلاسی و فردی
    stu_rep = svc.attendance.get_student_attendance_report(p1, s1["id"])
    assert stu_rep["summary"]["EXCUSED_ABSENT"] == 1
    cls_rep = svc.attendance.get_class_attendance_report(deputy, cls["id"])
    assert cls_rep["summary"]["TOTAL"] == 2

    # ثبت گزارش انضباطی توسط ناظم
    disc = svc.attendance.record_disciplinary_note(deputy, s2["id"], "تأخیر", "تأخیر در زنگ اول", note_date="2026-10-10")
    assert disc["category"] == "تأخیر"
