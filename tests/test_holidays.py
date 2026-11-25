from __future__ import annotations

import pytest

from utils.dates import format_jalali_date, parse_date_input
from utils.validators import ValidationError


def test_holidays_jalali_dates_and_attendance_blocking(app_env, actors):
    svc = app_env["services"]
    mgr = actors["manager"]
    t1 = actors["teacher1"]
    s1 = actors["student1"]

    # تست تبدیل دقیق تاریخ شمسی به میلادی و برعکس
    iso = parse_date_input("1405/11/22")
    assert format_jalali_date(iso, persian_digits=False) == "1405/11/22"

    hol = svc.notifications.create_holiday(
        actor=mgr,
        holiday_date="1405/11/22",
        title="راهپیمایی ۲۲ بهمن",
        holiday_type="OFFICIAL",
        notify_all=True,
    )
    assert hol["holiday_date"] == iso

    cls = svc.timetable.create_class(mgr, "دهم-تقویم", "10")
    subj = svc.timetable.create_subject(mgr, "عربی", "10")
    svc.timetable.assign_subject_to_class(mgr, cls["id"], subj["id"], t1["id"])
    svc.timetable.enroll_student_in_class(mgr, cls["id"], s1["id"])

    # ثبت حضور و غیاب عادی در روز تعطیل رسمی مسدود می‌شود
    with pytest.raises(ValidationError):
        svc.attendance.record_attendance(t1, cls["id"], s1["id"], "PRESENT", att_date=iso, period_number=1)

    # مدیر مدرسه با مجوز ویژه می‌تواند در کلاس فوق‌العاده روز تعطیل حضور ثبت کند
    rec = svc.attendance.record_attendance(
        mgr, cls["id"], s1["id"], "PRESENT", att_date=iso, period_number=1, override_holiday=True
    )
    assert rec["status"] == "PRESENT"

    # تست عدم تکرار وظایف زمان‌بندی‌شده در یک نوبت
    t_run1 = svc.notifications.run_scheduled_tasks_tick(backup_service=svc.backups, auto_backup_interval_hours=24)
    t_run2 = svc.notifications.run_scheduled_tasks_tick(backup_service=svc.backups, auto_backup_interval_hours=24)
    assert any(x.startswith("scheduled_backup:") for x in t_run1["executed"])
    assert not any(x.startswith("scheduled_backup:") for x in t_run2["executed"])
