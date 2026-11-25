from __future__ import annotations

import pytest

from services import AccessDeniedError
from utils.validators import ValidationError


def test_registration_submit_approve_and_group_routing(app_env, actors):
    svc = app_env["services"]
    mgr = actors["manager"]
    cls = svc.timetable.create_class(mgr, "یازدهم-الف", "11")

    req = svc.registration.submit_registration(
        bale_user_id=500101,
        full_name="دانش‌آموز متقاضی",
        requested_role="STUDENT",
        grade_level="11",
        requested_class_name="یازدهم-الف",
        student_code="NEW500",
    )
    assert req["status"] == "PENDING"

    # بررسی ارسال گزارش فقط به شناسه گروه مدیریتی و نه خصوصی مدیران
    queued = svc.db.fetchall("SELECT * FROM message_queue WHERE message_type = 'REGISTRATION_MANAGEMENT_GROUP';")
    assert len(queued) == 1
    assert queued[0]["recipient_chat_id"] == -100555000

    # جلوگیری از ثبت‌نام تکراری هم‌زمان
    with pytest.raises(ValidationError):
        svc.registration.submit_registration(
            bale_user_id=500101,
            full_name="دانش‌آموز متقاضی",
            requested_role="STUDENT",
            grade_level="11",
        )

    approved_user = svc.registration.approve_registration(mgr, req["id"])
    assert approved_user["role"] == "STUDENT"
    enrolled_cls = svc.timetable.classes.get_student_active_class(approved_user["id"])
    assert enrolled_cls["id"] == cls["id"]

    # جلوگیری از تأیید چندباره یک درخواست
    with pytest.raises(ValidationError):
        svc.registration.approve_registration(mgr, req["id"])


def test_registration_reject_and_cancel_and_access_control(app_env, actors):
    svc = app_env["services"]
    mgr = actors["manager"]
    student = actors["student1"]

    req1 = svc.registration.submit_registration(
        bale_user_id=500202,
        full_name="کاربر لغوکننده",
        requested_role="TEACHER",
        phone="09123334455",
        specialty="ادبیات",
    )
    assert svc.registration.cancel_own_request(500202) is True
    assert svc.registration.reg_repo.get_by_id(req1["id"])["status"] == "CANCELLED"

    req2 = svc.registration.submit_registration(
        bale_user_id=500203,
        full_name="کاربر ردشده",
        requested_role="PARENT",
        phone="09124445566",
        target_student_code="STU101",
    )

    # دانش‌آموز مجاز به تأیید ثبت‌نام نیست
    with pytest.raises(AccessDeniedError):
        svc.registration.approve_registration(student, req2["id"])

    rejected = svc.registration.reject_registration(mgr, req2["id"], "عدم تطابق شماره تماس با پرونده")
    assert rejected["status"] == "REJECTED"
    assert rejected["rejection_reason"] == "عدم تطابق شماره تماس با پرونده"
