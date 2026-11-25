from __future__ import annotations

import pytest

from services import AccessDeniedError
from utils.validators import ValidationError


def test_user_search_edit_deactivate_and_web_lockout(app_env, actors):
    svc = app_env["services"]
    admin = actors["admin"]
    mgr = actors["manager"]
    s1 = actors["student1"]

    found = svc.registration.users.search(query="STU101")
    assert len(found) == 1 and found[0]["id"] == s1["id"]

    updated = svc.registration.update_user_info(mgr, s1["id"], full_name="دانش‌آموز ویرایش‌شده", phone="09129998877")
    assert updated["full_name"] == "دانش‌آموز ویرایش‌شده"
    assert updated["phone"] == "09129998877"

    deactivated = svc.registration.set_user_active(mgr, s1["id"], False)
    assert deactivated["is_active"] == 0
    reactivated = svc.registration.set_user_active(mgr, s1["id"], True)
    assert reactivated["is_active"] == 1

    # غیرفعال‌سازی ادمین اصلی مسدود است
    with pytest.raises(AccessDeniedError):
        svc.registration.set_user_active(admin, admin["id"], False)

    # تست هش امن رمز وب و قفل موقت پس از تلاش ناموفق
    svc.registration.set_web_password(admin, mgr["id"], "StrongPass#2026")
    auth_ok = svc.registration.authenticate_web_user(mgr["bale_user_id"], "StrongPass#2026", max_attempts=3)
    assert auth_ok["id"] == mgr["id"]

    for _ in range(3):
        with pytest.raises(AccessDeniedError):
            svc.registration.authenticate_web_user(mgr["bale_user_id"], "WrongPassword", max_attempts=3, lockout_minutes=10)

    # حتی با رمز درست در زمان قفل موقت، ورود رد می‌شود
    with pytest.raises(AccessDeniedError):
        svc.registration.authenticate_web_user(mgr["bale_user_id"], "StrongPass#2026", max_attempts=3, lockout_minutes=10)

    # کد دانش‌آموزی تکراری مسدود می‌شود
    with pytest.raises(ValidationError):
        svc.registration.create_user_direct(mgr, 600999, "تکراری", "STUDENT", student_code="STU101")
