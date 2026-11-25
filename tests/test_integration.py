from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List

import httpx
import pytest

from main import build_bot_client
from utils.helpers import FatalBotAuthError, SafeBaleConnection, TransientGatewayError
from web import create_web_app


def _extract_csrf(html_bytes: bytes) -> str:
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html_bytes.decode("utf-8"))
    assert m, "توکن CSRF در صفحه یافت نشد"
    return m.group(1)


def test_bot_dispatcher_end_to_end_flows(app_env, actors):
    cfg = app_env["config"]
    svc = app_env["services"]
    client, _ = build_bot_client(cfg, svc)

    sent_messages: List[Dict[str, Any]] = []

    async def fake_execute(method: str, service: str, json: bool = None, **data):
        if service == "sendMessage":
            sent_messages.append({"chat_id": data.get("chat_id"), "text": data.get("text")})
            return {
                "message_id": len(sent_messages),
                "chat": {"id": data.get("chat_id"), "type": "private"},
                "text": data.get("text"),
            }
        if service in {"answerCallbackQuery", "editMessageText"}:
            sent_messages.append({"service": service, "data": data})
            return True
        return {}

    client.execute = fake_execute

    async def scenario():
        await client.initialize()
        try:
            # ۱. ارسال /start توسط ادمین
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 1,
                    "message": {
                        "message_id": 10,
                        "from": {"id": actors["admin"]["bale_user_id"], "first_name": "ادمین"},
                        "chat": {"id": actors["admin"]["bale_user_id"], "type": "private"},
                        "text": "/start",
                    },
                },
            )
            # ۲. تلاش دانش‌آموز برای اجرای مستقیم دستور غیرمجاز /مدیریت
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 2,
                    "message": {
                        "message_id": 11,
                        "from": {"id": actors["student1"]["bale_user_id"], "first_name": "دانش‌آموز"},
                        "chat": {"id": actors["student1"]["bale_user_id"], "type": "private"},
                        "text": "/مدیریت",
                    },
                },
            )
            # ۳. دریافت /دستورها توسط دانش‌آموز (فقط دستورات مجاز دانش‌آموز نمایش داده شود)
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 3,
                    "message": {
                        "message_id": 12,
                        "from": {"id": actors["student1"]["bale_user_id"], "first_name": "دانش‌آموز"},
                        "chat": {"id": actors["student1"]["bale_user_id"], "type": "private"},
                        "text": "/دستورها",
                    },
                },
            )
            await asyncio.sleep(0.15)
        finally:
            await client.shutdown()

    asyncio.run(scenario())

    admin_msgs = [m["text"] for m in sent_messages if m.get("chat_id") == actors["admin"]["bale_user_id"]]
    student_msgs = [m["text"] for m in sent_messages if m.get("chat_id") == actors["student1"]["bale_user_id"]]

    assert any("ادمین کل سامانه" in t for t in admin_msgs)
    assert any("⛔" in t for t in student_msgs)
    help_msg = [t for t in student_msgs if "راهنمای جامع دستورهای مجاز" in t][0]
    assert "/کارنامه" in help_msg
    assert "/پشتیبان" not in help_msg
    assert "/مدیریت" not in help_msg


def test_safe_bale_connection_handles_corrupt_json_and_auth_errors():
    async def check():
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/badGateway"):
                return httpx.Response(502, text="<html>Bad Gateway</html>")
            if request.url.path.endswith("/corruptJson"):
                return httpx.Response(200, text="not-valid-json{{{")
            if request.url.path.endswith("/getMe"):
                return httpx.Response(404, text="Not Found")
            return httpx.Response(200, json={"ok": True, "result": {"id": 1}})

        conn = SafeBaleConnection("123456:ABCDEF12345678901234", base_url="https://mock.bale.local")
        conn.is_started = True
        conn.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(TransientGatewayError):
                await conn.request("GET", "badGateway")
            with pytest.raises(TransientGatewayError):
                await conn.request("GET", "corruptJson")
            with pytest.raises(FatalBotAuthError):
                await conn.request("GET", "getMe")
        finally:
            await conn.stop()

    asyncio.run(check())


def test_web_panel_auth_csrf_rbac_forms_and_exports(app_env, actors):
    cfg = app_env["config"]
    svc = app_env["services"]
    admin = actors["admin"]
    teacher = actors["teacher1"]

    svc.registration.set_web_password(admin, admin["id"], "AdminWebPass!2026")
    svc.registration.set_web_password(admin, teacher["id"], "TeacherPass!2026")

    app = create_web_app(cfg, svc)
    web_client = app.test_client()

    # ۱. ریدایرکت کاربر واردنشده به صفحه ورود
    r0 = web_client.get("/")
    assert r0.status_code == 302 and "/login" in r0.headers["Location"]

    # ۲. محافظت CSRF: ارسال فرم بدون توکن باید با کد 400 رد شود
    r_no_csrf = web_client.post("/login", data={"bale_user_id": str(admin["bale_user_id"]), "password": "AdminWebPass!2026"})
    assert r_no_csrf.status_code == 400

    # ۳. ورود موفق ادمین با توکن معتبر CSRF
    login_page = web_client.get("/login")
    csrf = _extract_csrf(login_page.data)
    r_login = web_client.post(
        "/login",
        data={"csrf_token": csrf, "bale_user_id": str(admin["bale_user_id"]), "password": "AdminWebPass!2026"},
        follow_redirects=True,
    )
    assert r_login.status_code == 200
    assert "پیشخوان" in r_login.data.decode("utf-8")

    # ۴. ایجاد کلاس از طریق فرم پنل وب
    classes_page = web_client.get("/classes")
    csrf2 = _extract_csrf(classes_page.data)
    r_cls = web_client.post(
        "/classes",
        data={"csrf_token": csrf2, "action": "create", "name": "یازدهم-وب", "grade_level": "11", "capacity": "30"},
        follow_redirects=True,
    )
    assert r_cls.status_code == 200
    assert "یازدهم-وب" in r_cls.data.decode("utf-8")

    # ۵. دریافت خروجی CSV و PDF از پنل وب
    csv_resp = web_client.get("/reports/export?category=USERS&fmt=csv")
    assert csv_resp.status_code == 200 and csv_resp.data.startswith(b"\xef\xbb\xbf")
    pdf_resp = web_client.get("/reports/export?category=OVERVIEW&fmt=pdf")
    assert pdf_resp.status_code == 200 and pdf_resp.data.startswith(b"%PDF")

    # ۶. خروج امن و بررسی محدودیت دسترسی دبیر به صفحات مدیریتی (403)
    dash_html = web_client.get("/").data
    csrf3 = _extract_csrf(dash_html)
    r_logout = web_client.post("/logout", data={"csrf_token": csrf3}, follow_redirects=True)
    assert r_logout.status_code == 200

    login_t = web_client.get("/login")
    csrf_t = _extract_csrf(login_t.data)
    web_client.post(
        "/login",
        data={"csrf_token": csrf_t, "bale_user_id": str(teacher["bale_user_id"]), "password": "TeacherPass!2026"},
        follow_redirects=True,
    )
    r_forbidden = web_client.get("/users")
    assert r_forbidden.status_code == 403


def test_bot_registration_wizard_and_cancel_flow(app_env):
    cfg = app_env["config"]
    svc = app_env["services"]
    client, _ = build_bot_client(cfg, svc)
    outputs: List[Dict[str, Any]] = []

    async def fake_execute(method: str, service: str, json: bool = None, **data):
        outputs.append({"service": service, "data": data})
        if service == "sendMessage":
            return {"message_id": len(outputs), "chat": {"id": data.get("chat_id"), "type": "private"}, "text": data.get("text")}
        return True

    client.execute = fake_execute

    async def scenario():
        await client.initialize()
        try:
            # کاربر جدید دکمه ثبت‌نام را می‌زند
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 20,
                    "message": {
                        "message_id": 200,
                        "from": {"id": 400500, "first_name": "متقاضی"},
                        "chat": {"id": 400500, "type": "private"},
                        "text": "📝 شروع ثبت‌نام",
                    },
                },
            )
            await asyncio.sleep(0.05)
            # انتخاب نقش دبیر از طریق دکمه شیشه‌ای
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 21,
                    "callback_query": {
                        "id": "cb_reg_1",
                        "from": {"id": 400500, "first_name": "متقاضی"},
                        "message": {"message_id": 201, "chat": {"id": 400500, "type": "private"}, "text": "انتخاب نقش"},
                        "data": "reg_role:TEACHER",
                    },
                },
            )
            await asyncio.sleep(0.05)
            assert svc.registration.users.get_session(400500) is not None
            # انصراف در میانه فرم با دستور /لغو بدون گیرکردن در وضعیت قبلی
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 22,
                    "message": {
                        "message_id": 202,
                        "from": {"id": 400500, "first_name": "متقاضی"},
                        "chat": {"id": 400500, "type": "private"},
                        "text": "/لغو",
                    },
                },
            )
            await asyncio.sleep(0.1)
            assert svc.registration.users.get_session(400500) is None
        finally:
            await client.shutdown()

    asyncio.run(scenario())
    sent_texts = [o["data"].get("text", "") for o in outputs if o["service"] == "sendMessage"]
    assert any("لغو شد" in t for t in sent_texts)


def test_all_web_pages_render_and_cli_commands(app_env, actors, monkeypatch):
    from main import main as cli_main

    cfg = app_env["config"]
    svc = app_env["services"]
    admin = actors["admin"]
    mgr = actors["manager"]
    t1 = actors["teacher1"]
    s1 = actors["student1"]

    cls = svc.timetable.create_class(mgr, "دوازدهم-ریاضی", "12")
    subj = svc.timetable.create_subject(mgr, "گسسته", "12", "تخصصی")
    svc.timetable.assign_subject_to_class(mgr, cls["id"], subj["id"], t1["id"])
    svc.timetable.enroll_student_in_class(mgr, cls["id"], s1["id"])
    svc.timetable.set_timetable_slot(mgr, cls["id"], "شنبه", 1, "BOTH", subj["id"])

    svc.registration.set_web_password(admin, admin["id"], "SuperSecure#1405")
    app = create_web_app(cfg, svc)
    wc = app.test_client()

    csrf = _extract_csrf(wc.get("/login").data)
    wc.post("/login", data={"csrf_token": csrf, "bale_user_id": str(admin["bale_user_id"]), "password": "SuperSecure#1405"})

    for route in [
        "/",
        "/users",
        "/classes",
        "/subjects",
        f"/timetable?class_id={cls['id']}",
        f"/timetable/export/{cls['id']}/png",
        f"/timetable/export/{cls['id']}/pdf",
        f"/attendance?class_id={cls['id']}",
        f"/grades?class_id={cls['id']}&student_id={s1['id']}",
        "/announcements",
        "/holidays",
        "/reports",
        "/audit",
        "/settings",
    ]:
        resp = wc.get(route)
        assert resp.status_code == 200, f"مسیر وب {route} کد {resp.status_code} برگرداند"

    # تست دستورات خط فرمان main.py (migrate, backup, create-web-admin)
    monkeypatch.setenv("BALE_BOT_TOKEN", cfg.bale_bot_token)
    monkeypatch.setenv("PRIMARY_ADMIN_ID", str(cfg.primary_admin_id))
    monkeypatch.setenv("DATABASE_PATH", str(cfg.database_path))
    monkeypatch.setenv("BACKUP_DIR", str(cfg.backup_dir))
    monkeypatch.setenv("LOG_DIR", str(cfg.log_dir))
    monkeypatch.setenv("WEB_SECRET_KEY", cfg.web_secret_key)

    assert cli_main(["migrate"]) == 0
    assert cli_main(["backup"]) == 0
    assert cli_main(["create-web-admin", "--password", "NewAdminPass#2026"]) == 0


def test_bot_slash_commands_timetable_grades_calendar_backup(app_env, actors):
    cfg = app_env["config"]
    svc = app_env["services"]
    mgr = actors["manager"]
    t1 = actors["teacher1"]
    s1 = actors["student1"]
    admin = actors["admin"]

    cls = svc.timetable.create_class(mgr, "دهم-تست", "10")
    subj = svc.timetable.create_subject(mgr, "فیزیک", "10", "تخصصی")
    svc.timetable.assign_subject_to_class(mgr, cls["id"], subj["id"], t1["id"])
    svc.timetable.enroll_student_in_class(mgr, cls["id"], s1["id"])

    client, _ = build_bot_client(cfg, svc)
    replies: List[Dict[str, Any]] = []

    async def fake_execute(method: str, service: str, json: bool = None, **data):
        if service == "sendMessage":
            replies.append({"chat_id": data.get("chat_id"), "text": data.get("text")})
            return {"message_id": len(replies), "chat": {"id": data.get("chat_id"), "type": "private"}, "text": data.get("text")}
        return True

    client.execute = fake_execute

    async def scenario():
        await client.initialize()
        try:
            cmds = [
                (mgr["bale_user_id"], "/برنامه_ویرایش دهم-تست | دوشنبه | 2 | هفته اول | فیزیک"),
                (s1["bale_user_id"], "/برنامه"),
                (s1["bale_user_id"], "/کارنامه"),
                (t1["bale_user_id"], "/حضور"),
                (s1["bale_user_id"], "/تقویم"),
                (admin["bale_user_id"], "/پشتیبان"),
            ]
            for idx, (uid, txt) in enumerate(cmds, start=50):
                await client.dispatcher.dispatch_raw_update(
                    client,
                    {
                        "update_id": idx,
                        "message": {
                            "message_id": idx * 10,
                            "from": {"id": uid, "first_name": "کاربر"},
                            "chat": {"id": uid, "type": "private"},
                            "text": txt,
                        },
                    },
                )
            await asyncio.sleep(0.2)
        finally:
            await client.shutdown()

    asyncio.run(scenario())
    all_texts = "\n".join(r["text"] for r in replies)
    assert "به‌روزرسانی شد" in all_texts
    assert "فیزیک" in all_texts
    assert "پشتیبان‌گیری" in all_texts


