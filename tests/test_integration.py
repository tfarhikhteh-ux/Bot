from __future__ import annotations

import asyncio
import io
from typing import Any, Dict, List

import httpx
import pytest
from openpyxl import load_workbook

from main import build_bot_client, main as cli_main
from utils.helpers import FatalBotAuthError, SafeBaleConnection, TransientGatewayError


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
            # ۳. دریافت /دستورها توسط دانش‌آموز (فقط دستورات مجاز دانش‌آموز و بدون هیچ اشاره به وب)
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
    assert "وب" not in help_msg
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


def test_bale_only_cli_and_in_chat_backup_delivery(app_env, actors, monkeypatch):
    cfg = app_env["config"]
    svc = app_env["services"]
    admin = actors["admin"]

    monkeypatch.setenv("BALE_BOT_TOKEN", cfg.bale_bot_token)
    monkeypatch.setenv("PRIMARY_ADMIN_ID", str(cfg.primary_admin_id))
    monkeypatch.setenv("DATABASE_PATH", str(cfg.database_path))
    monkeypatch.setenv("BACKUP_DIR", str(cfg.backup_dir))
    monkeypatch.setenv("LOG_DIR", str(cfg.log_dir))

    assert cli_main(["migrate"]) == 0
    assert cli_main(["backup"]) == 0

    # اطمینان از اینکه دستورات وب از CLI حذف شده‌اند
    with pytest.raises(SystemExit):
        cli_main(["web"])
    with pytest.raises(SystemExit):
        cli_main(["create-web-admin"])

    # تست تهیه و دریافت مستقیم فایل پشتیبان دیتابیس در چت بله توسط ادمین
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
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 35,
                    "callback_query": {
                        "id": "cb_bkp_create",
                        "from": {"id": admin["bale_user_id"], "first_name": "ادمین"},
                        "message": {"message_id": 350, "chat": {"id": admin["bale_user_id"], "type": "private"}, "text": "پشتیبان"},
                        "data": "bkp:create",
                    },
                },
            )
            await asyncio.sleep(0.05)
            latest = svc.backups.list_backups(admin)[0]
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 36,
                    "callback_query": {
                        "id": "cb_bkp_dl",
                        "from": {"id": admin["bale_user_id"], "first_name": "ادمین"},
                        "message": {"message_id": 351, "chat": {"id": admin["bale_user_id"], "type": "private"}, "text": "پشتیبان"},
                        "data": f"bkp:dl:{latest['id']}",
                    },
                },
            )
            await asyncio.sleep(0.1)
        finally:
            await client.shutdown()

    asyncio.run(scenario())
    assert len(sent_docs) == 2
    assert all(name.endswith(".db") and name.startswith("school_backup_") for name in sent_docs)


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


def test_excel_xlsx_csv_and_pdf_exports_in_bale_bot(app_env, actors):
    cfg = app_env["config"]
    svc = app_env["services"]
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

    # اعتبارسنجی خروجی‌های اکسل راست‌به‌چپ (.xlsx) و PDF در سطح سرویس
    xlsx_bytes = svc.grades.export_student_report_card(s1, s1["id"], fmt="xlsx")
    wb = load_workbook(io.BytesIO(xlsx_bytes))
    assert wb.active.sheet_view.rightToLeft is True
    assert svc.grades.export_student_report_card(s1, s1["id"], fmt="pdf").startswith(b"%PDF")

    # بررسی ارسال مستقیم فایل‌های اکسل (.xlsx)، CSV و PDF در چت بله
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
                (mgr["bale_user_id"], "rep:csv:USERS"),
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
    assert "school_report_users.csv" in sent_docs
    assert "school_report_grades.pdf" in sent_docs
    assert f"timetable_{cls['id']}.xlsx" in sent_docs
    assert f"report_card_{s1['id']}.xlsx" in sent_docs
    assert f"report_card_{s1['id']}.pdf" in sent_docs


def test_group_reply_modifier_and_bale_only_24_7_report_card(app_env, actors):
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

    evals = {e["name"]: e for e in svc.grades.grades.list_evaluation_types()}
    final_row = svc.grades.record_grade(t1, s1["id"], cls["id"], subj["id"], evals["آزمون پایان‌ترم اول"]["id"], 19.0)

    client, _ = build_bot_client(cfg, svc)
    bot_messages: List[Dict[str, Any]] = []

    async def fake_execute(method: str, service: str, json: bool = None, **data):
        if service == "sendMessage":
            bot_messages.append(
                {
                    "chat_id": data.get("chat_id"),
                    "text": data.get("text", ""),
                    "reply_markup": data.get("reply_markup"),
                }
            )
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
            # ۴. فوروارد پیام دانش‌آموز به شخصی ربات و ارسال «مثبت درسی»
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 204,
                    "message": {
                        "message_id": 5004,
                        "from": {"id": t1["bale_user_id"], "first_name": "دبیر"},
                        "chat": {"id": t1["bale_user_id"], "type": "private"},
                        "forward_from": {"id": s1["bale_user_id"], "first_name": "دانش‌آموز"},
                        "forward_date": 1764050000,
                        "text": "تکلیف جلسه سوم شیمی",
                    },
                },
            )
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 205,
                    "message": {
                        "message_id": 5005,
                        "from": {"id": t1["bale_user_id"], "first_name": "دبیر"},
                        "chat": {"id": t1["bale_user_id"], "type": "private"},
                        "text": "مثبت درسی",
                    },
                },
            )
            # ۵. فوروارد پیام غیرمتنی (عکس تمرین) به پی‌وی ربات و ارسال «منفی»
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 206,
                    "message": {
                        "message_id": 5006,
                        "from": {"id": t1["bale_user_id"], "first_name": "دبیر"},
                        "chat": {"id": t1["bale_user_id"], "type": "private"},
                        "forward_from": {"id": s1["bale_user_id"], "first_name": "دانش‌آموز"},
                        "forward_date": 1764050010,
                    },
                },
            )
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 207,
                    "message": {
                        "message_id": 5007,
                        "from": {"id": t1["bale_user_id"], "first_name": "دبیر"},
                        "chat": {"id": t1["bale_user_id"], "type": "private"},
                        "text": "منفی",
                    },
                },
            )
            # ۶. بروزرسانی لحظه‌ای کارنامه ۲۴ ساعته دانش‌آموز مستقیماً داخل بله (بدون هیچ لینک وب)
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 208,
                    "callback_query": {
                        "id": "cb_bale_rc_refresh",
                        "from": {"id": s1["bale_user_id"], "first_name": "دانش‌آموز"},
                        "message": {"message_id": 5010, "chat": {"id": s1["bale_user_id"], "type": "private"}, "text": "کارنامه"},
                        "data": f"grd:refresh:{s1['id']}",
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

    teacher_pv_texts = "\n".join(m["text"] for m in bot_messages if m["chat_id"] == t1["bale_user_id"])
    assert f"فرد هدف شناسایی شد: «{s1['full_name']}»" in teacher_pv_texts
    assert "مثبت درسی (+۱ نمره مستمر) ثبت شد" in teacher_pv_texts
    assert "منفی درسی (-۱ نمره مستمر) ثبت شد" in teacher_pv_texts
    assert svc.grades.grades.get_by_id(final_row["id"])["score"] == 19.0

    student_pv_msgs = [m for m in bot_messages if m["chat_id"] == s1["bale_user_id"]]
    student_pv_texts = "\n".join(m["text"] for m in student_pv_msgs)
    assert "شیمی ۲" in student_pv_texts
    assert "http" not in student_pv_texts
    assert "online-card" not in student_pv_texts
    # بررسی اینکه دکمه‌های کارنامه در بله فقط شامل بروزرسانی، PDF و XLSX هستند (بدون دکمه وب یا PNG کارنامه)
    markups = [str(m["reply_markup"]) for m in student_pv_msgs if m.get("reply_markup")]
    joined_mk = "\n".join(markups)
    assert "grd:refresh:" in joined_mk
    assert "grd:rc_pdf:" in joined_mk
    assert "grd:rc_xlsx:" in joined_mk
    assert "web_link" not in joined_mk
    assert "set_portal_pw" not in joined_mk
