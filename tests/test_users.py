from __future__ import annotations

import pytest

from services import AccessDeniedError
from utils.validators import ValidationError


def test_user_search_edit_deactivate_and_parent_links(app_env, actors):
    svc = app_env["services"]
    admin = actors["admin"]
    mgr = actors["manager"]
    s1 = actors["student1"]
    p1 = actors["parent1"]

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

    # بررسی اتصال ولی به فرزند در سامانه بله
    children = svc.registration.users.get_children_of_parent(p1["id"])
    assert any(c["id"] == s1["id"] for c in children)

    # کد دانش‌آموزی تکراری مسدود می‌شود
    with pytest.raises(ValidationError):
        svc.registration.create_user_direct(mgr, 600999, "تکراری", "STUDENT", student_code="STU101")
