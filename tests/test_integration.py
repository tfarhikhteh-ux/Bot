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


def test_excel_xlsx_and_pdf_exports_across_web_and_bot(app_env, actors):
    import io
    from openpyxl import load_workbook

    cfg = app_env["config"]
    svc = app_env["services"]
    admin = actors["admin"]
    mgr = actors["manager"]
    t1 = actors["teacher1"]
    s1 = actors["student1"]

    cls = svc.timetable.create_class(mgr, "یازدهم-تجربی", "11")
    subj = svc.timetable.create_subject(mgr, "زیست‌شناسی", "11", "تخصصی", default_coefficient=4.0)
    svc.timetable.assign_subject_to_class(mgr, cls["id"], subj["id"], t1["id"], coefficient=4.0)
    svc.timetable.enroll_student_in_class(mgr, cls["id"], s1["id"])
    svc.timetable.set_timetable_slot(mgr, cls["id"], "شنبه", 1, "BOTH", subj["id"])

    ev_id = svc.grades.grades.list_evaluation_types()[0]["id"]
    svc.grades.record_grade(t1, s1["id"], cls["id"], subj["id"], ev_id, 19.25)
    svc.attendance.record_attendance(t1, cls["id"], s1["id"], "PRESENT", att_date="2026-10-10", period_number=1)

    # ۱. بررسی خروجی‌های اکسل (.xlsx) و PDF در پنل وب
    svc.registration.set_web_password(admin, admin["id"], "ExcelPdf#2026")
    app = create_web_app(cfg, svc)
    wc = app.test_client()
    csrf = _extract_csrf(wc.get("/login").data)
    wc.post("/login", data={"csrf_token": csrf, "bale_user_id": str(admin["bale_user_id"]), "password": "ExcelPdf#2026"})

    for xlsx_url in [
        "/reports/export?category=USERS&fmt=xlsx",
        f"/reports/export?category=GRADES&fmt=xlsx&class_id={cls['id']}",
        f"/reports/export?category=ATTENDANCE&fmt=xlsx&class_id={cls['id']}",
        f"/timetable/export/{cls['id']}/xlsx",
        f"/grades/report-card/{s1['id']}/xlsx",
    ]:
        r = wc.get(xlsx_url)
        assert r.status_code == 200, xlsx_url
        wb = load_workbook(io.BytesIO(r.data))
        ws = wb.active
        assert ws.sheet_view.rightToLeft is True
        assert ws.max_row >= 3

    for pdf_url in [
        f"/reports/export?category=GRADES&fmt=pdf&class_id={cls['id']}",
        f"/timetable/export/{cls['id']}/pdf",
        f"/grades/report-card/{s1['id']}/pdf",
    ]:
        r = wc.get(pdf_url)
        assert r.status_code == 200 and r.data.startswith(b"%PDF")

    # ۲. بررسی ارسال فایل‌های اکسل (.xlsx) و PDF در ربات بله
    client, _ = build_bot_client(cfg, svc)
    sent_docs: List[str] = []

    async def fake_execute(method: str, service: str, json: bool = None, **data):
        if service == "sendDocument":
            doc_obj = data.get("document")
            sent_docs.append(getattr(doc_obj, "name", "unknown"))
            return {"message_id": len(sent_docs), "chat": {"id": data.get("chat_id"), "type": "private"}}
        return {"message_id": 1, "chat": {"id": data.get("chat_id", 1), "type": "private"}, "text": "ok"}

    client.execute = fake_execute

    async def scenario():
        await client.initialize()
        try:
            callbacks = [
                (mgr["bale_user_id"], "rep:xlsx:GRADES"),
                (mgr["bale_user_id"], "rep:pdf:GRADES"),
                (s1["bale_user_id"], f"tt:xlsx:{cls['id']}"),
                (s1["bale_user_id"], f"grd:rc_xlsx:{s1['id']}"),
                (s1["bale_user_id"], f"grd:rc_pdf:{s1['id']}"),
            ]
            for idx, (uid, cb_data) in enumerate(callbacks, start=90):
                await client.dispatcher.dispatch_raw_update(
                    client,
                    {
                        "update_id": idx,
                        "callback_query": {
                            "id": f"cb_exp_{idx}",
                            "from": {"id": uid, "first_name": "کاربر"},
                            "message": {"message_id": idx * 10, "chat": {"id": uid, "type": "private"}, "text": "منو"},
                            "data": cb_data,
                        },
                    },
                )
            await asyncio.sleep(0.2)
        finally:
            await client.shutdown()

    asyncio.run(scenario())
    assert "school_report_grades.xlsx" in sent_docs
    assert "school_report_grades.pdf" in sent_docs
    assert f"timetable_{cls['id']}.xlsx" in sent_docs
    assert f"report_card_{s1['id']}.xlsx" in sent_docs
    assert f"report_card_{s1['id']}.pdf" in sent_docs


def test_group_reply_modifier_and_24_7_online_report_card_portal(app_env, actors):
    from utils.security import generate_signed_report_token

    cfg = app_env["config"]
    svc = app_env["services"]
    mgr = actors["manager"]
    t1 = actors["teacher1"]
    s1, s2 = actors["student1"], actors["student2"]

    cls = svc.timetable.create_class(mgr, "یازدهم-گروه", "11")
    subj = svc.timetable.create_subject(mgr, "شیمی ۲", "11", "تخصصی", default_coefficient=3.0)
    svc.timetable.assign_subject_to_class(mgr, cls["id"], subj["id"], t1["id"], coefficient=3.0)
    svc.timetable.enroll_student_in_class(mgr, cls["id"], s1["id"])
    svc.timetable.enroll_student_in_class(mgr, cls["id"], s2["id"])

    # ثبت نمره پایانی ۱۹ برای اطمینان از دست‌نخورده ماندن پایانی پس از مثبت/منفی درسی در گروه
    evals = {e["name"]: e for e in svc.grades.grades.list_evaluation_types()}
    final_row = svc.grades.record_grade(t1, s1["id"], cls["id"], subj["id"], evals["آزمون پایان‌ترم اول"]["id"], 19.0)

    client, _ = build_bot_client(cfg, svc)
    bot_messages: List[Dict[str, Any]] = []

    async def fake_execute(method: str, service: str, json: bool = None, **data):
        if service == "sendMessage":
            bot_messages.append({"chat_id": data.get("chat_id"), "text": data.get("text", "")})
            return {"message_id": len(bot_messages), "chat": {"id": data.get("chat_id"), "type": "group"}, "text": data.get("text")}
        return True

    client.execute = fake_execute

    async def scenario():
        await client.initialize()
        try:
            # ۱. ریپلای دبیر روی پیام دانش‌آموز با دستور «مثبت درسی» در گروه کلاس
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 201,
                    "message": {
                        "message_id": 5001,
                        "from": {"id": t1["bale_user_id"], "first_name": "دبیر"},
                        "chat": {"id": -100777, "type": "group"},
                        "reply_to_message": {
                            "message_id": 5000,
                            "from": {"id": s1["bale_user_id"], "first_name": "دانش‌آموز"},
                            "chat": {"id": -100777, "type": "group"},
                            "text": "پاسخ تمرین اول",
                        },
                        "text": "مثبت درسی",
                    },
                },
            )
            # ۲. ریپلای دبیر با دستور «منفی درسی» روی پیام همان دانش‌آموز
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 202,
                    "message": {
                        "message_id": 5002,
                        "from": {"id": t1["bale_user_id"], "first_name": "دبیر"},
                        "chat": {"id": -100777, "type": "group"},
                        "reply_to_message": {
                            "message_id": 5000,
                            "from": {"id": s1["bale_user_id"], "first_name": "دانش‌آموز"},
                            "chat": {"id": -100777, "type": "group"},
                            "text": "پاسخ تمرین اول",
                        },
                        "text": "منفی درسی",
                    },
                },
            )
            # ۳. تلاش دانش‌آموز دیگر برای دادن «مثبت درسی» باید رد شود
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 203,
                    "message": {
                        "message_id": 5003,
                        "from": {"id": s2["bale_user_id"], "first_name": "دانش‌آموز۲"},
                        "chat": {"id": -100777, "type": "group"},
                        "reply_to_message": {
                            "message_id": 5000,
                            "from": {"id": s1["bale_user_id"], "first_name": "دانش‌آموز"},
                            "chat": {"id": -100777, "type": "group"},
                            "text": "پاسخ",
                        },
                        "text": "مثبت درسی",
                    },
                },
            )
            # ۴. درخواست لینک کارنامه آنلاین وب توسط دانش‌آموز در ربات
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 204,
                    "callback_query": {
                        "id": "cb_online_rc",
                        "from": {"id": s1["bale_user_id"], "first_name": "دانش‌آموز"},
                        "message": {"message_id": 5010, "chat": {"id": s1["bale_user_id"], "type": "private"}, "text": "کارنامه"},
                        "data": f"grd:web_link:{s1['id']}",
                    },
                },
            )
            await asyncio.sleep(0.2)
        finally:
            await client.shutdown()

    asyncio.run(scenario())

    group_texts = "\n".join(m["text"] for m in bot_messages if m["chat_id"] == -100777)
    assert "مثبت درسی (+۱ نمره مستمر) ثبت شد" in group_texts
    assert "منفی درسی (-۱ نمره مستمر) ثبت شد" in group_texts
    assert "⛔" in group_texts
    assert svc.grades.grades.get_by_id(final_row["id"])["score"] == 19.0

    priv_texts = "\n".join(m["text"] for m in bot_messages if m["chat_id"] == s1["bale_user_id"])
    assert "/online-card/" in priv_texts

    # ۵. تست کارنامه آنلاین تحت وب با لینک امضاشده و درگاه ۲۴ ساعته دانش‌آموز (/student-portal)
    app = create_web_app(cfg, svc)
    wc = app.test_client()

    valid_token = generate_signed_report_token(cfg.web_secret_key, s1["id"])
    r_card = wc.get(f"/online-card/{valid_token}")
    assert r_card.status_code == 200
    card_html = r_card.data.decode("utf-8")
    assert "کارنامه آنلاین آموزشی" in card_html and "شیمی ۲" in card_html

    assert wc.get("/online-card/tampered_bad_token").status_code == 403
    assert wc.get(f"/online-card/{valid_token}/export/pdf").status_code == 200
    assert wc.get(f"/online-card/{valid_token}/export/xlsx").status_code == 200

    # ورود مستقیم دانش‌آموز به /student-portal با کد دانش‌آموزی و رمز کارنامه آنلاین
    svc.registration.set_web_password(s1, s1["id"], "StudentPass#1405")
    portal_login_page = wc.get("/student-portal")
    csrf = _extract_csrf(portal_login_page.data)
    r_portal = wc.post(
        "/student-portal",
        data={"csrf_token": csrf, "identifier": s1["student_code"], "password": "StudentPass#1405"},
        follow_redirects=True,
    )
    assert r_portal.status_code == 200
    assert s1["full_name"] in r_portal.data.decode("utf-8")
    # دانش‌آموز واردشده در پرتال کارنامه نباید به صفحات مدیریتی دسترسی پیدا کند
    assert wc.get("/users").status_code == 302




