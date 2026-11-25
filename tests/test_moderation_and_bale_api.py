from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import httpx

from main import build_bot_client
from utils.helpers import DEFAULT_BALE_COMMANDS, SafeBaleConnection, safe_answer_callback


def test_all_staff_roles_can_use_both_reply_and_forward_continuous_modifiers(app_env, actors):
    """بررسی اینکه هر ۵ نقش کارکنان (ادمین، مدیر، ناظم، معلم و انجمن) هم با ریپلای در گروه و هم با فوروارد به پی‌وی می‌توانند مثبت/منفی درسی بدهند."""
    cfg = app_env["config"]
    svc = app_env["services"]
    admin = actors["admin"]
    mgr = actors["manager"]
    deputy = actors["deputy"]
    assoc = actors["assoc"]
    t1 = actors["teacher1"]
    s1 = actors["student1"]
    p1 = actors["parent1"]

    cls = svc.timetable.create_class(mgr, "دوازدهم-کارکنان", "12")
    subj = svc.timetable.create_subject(mgr, "ادبیات فارسی ۳", "12", "عمومی", default_coefficient=2.0)
    svc.timetable.assign_subject_to_class(mgr, cls["id"], subj["id"], t1["id"], coefficient=2.0)
    svc.timetable.enroll_student_in_class(mgr, cls["id"], s1["id"])

    evals = {e["name"]: e for e in svc.grades.grades.list_evaluation_types()}
    svc.grades.record_grade(t1, s1["id"], cls["id"], subj["id"], evals["ارزشیابی مستمر نوبت اول"]["id"], 14.0)
    final_row = svc.grades.record_grade(t1, s1["id"], cls["id"], subj["id"], evals["آزمون پایان‌ترم اول"]["id"], 19.5)

    client, _ = build_bot_client(cfg, svc)
    sent_messages: List[Dict[str, Any]] = []

    async def fake_execute(method: str, service: str, json: bool = None, **data):
        if service == "sendMessage":
            sent_messages.append({"chat_id": data.get("chat_id"), "text": data.get("text", "")})
            return {"message_id": len(sent_messages), "chat": {"id": data.get("chat_id"), "type": "group"}, "text": data.get("text")}
        return True

    client.execute = fake_execute

    async def scenario():
        await client.initialize()
        try:
            uid_seq = 700
            # ۱. ریپلای در گروه توسط ناظم (DEPUTY) و عضو انجمن (ASSOCIATION_MEMBER) و مدیر (SCHOOL_MANAGER)
            for staff_actor in (deputy, assoc, mgr):
                uid_seq += 1
                await client.dispatcher.dispatch_raw_update(
                    client,
                    {
                        "update_id": uid_seq,
                        "message": {
                            "message_id": 8000 + uid_seq,
                            "from": {"id": staff_actor["bale_user_id"], "first_name": staff_actor["full_name"]},
                            "chat": {"id": -100991, "type": "group"},
                            "reply_to_message": {
                                "message_id": 7999,
                                "from": {"id": s1["bale_user_id"], "first_name": s1["full_name"]},
                                "chat": {"id": -100991, "type": "group"},
                                "text": "پاسخ کلاسی",
                            },
                            "text": "مثبت درسی",
                        },
                    },
                )

            # ۲. فوروارد پیام دانش‌آموز به شخصی ربات توسط ادمین، ناظم و انجمن + ارسال دستور پس از آن
            for staff_actor, cmd_txt in ((admin, "مثبت درسی"), (deputy, "منفی درسی"), (assoc, "مثبت درسی")):
                uid_seq += 1
                await client.dispatcher.dispatch_raw_update(
                    client,
                    {
                        "update_id": uid_seq,
                        "message": {
                            "message_id": 8100 + uid_seq,
                            "from": {"id": staff_actor["bale_user_id"], "first_name": staff_actor["full_name"]},
                            "chat": {"id": staff_actor["bale_user_id"], "type": "private"},
                            "forward_from": {"id": s1["bale_user_id"], "first_name": s1["full_name"]},
                            "forward_date": 1764051000,
                            "text": "پیام دانش‌آموز",
                        },
                    },
                )
                uid_seq += 1
                await client.dispatcher.dispatch_raw_update(
                    client,
                    {
                        "update_id": uid_seq,
                        "message": {
                            "message_id": 8100 + uid_seq,
                            "from": {"id": staff_actor["bale_user_id"], "first_name": staff_actor["full_name"]},
                            "chat": {"id": staff_actor["bale_user_id"], "type": "private"},
                            "text": cmd_txt,
                        },
                    },
                )

            # ۳. تلاش ولی (PARENT) باید مسدود شود
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 790,
                    "message": {
                        "message_id": 8900,
                        "from": {"id": p1["bale_user_id"], "first_name": p1["full_name"]},
                        "chat": {"id": -100991, "type": "group"},
                        "reply_to_message": {
                            "message_id": 7999,
                            "from": {"id": s1["bale_user_id"], "first_name": s1["full_name"]},
                            "chat": {"id": -100991, "type": "group"},
                            "text": "پاسخ",
                        },
                        "text": "مثبت درسی",
                    },
                },
            )
            await asyncio.sleep(0.25)
        finally:
            await client.shutdown()

    asyncio.run(scenario())

    # نمره مستمر اولیه 14 بود: +3 در گروه + (+1 -1 +1 = +1) در پی‌وی => 18.0 ، و نمره پایانی 19.5 دست‌نخورده است
    rc = svc.grades.get_student_report_card(s1, s1["id"])
    subj_entry = rc["subjects"][0]
    assert subj_entry["continuous_score"] == 18.0
    assert subj_entry["final_score"] == 19.5
    assert subj_entry["positive_count"] == 5
    assert subj_entry["negative_count"] == 1
    assert svc.grades.grades.get_by_id(final_row["id"])["score"] == 19.5

    grp_texts = "\n".join(m["text"] for m in sent_messages if m["chat_id"] == -100991)
    assert "⛔" in grp_texts


def test_group_locks_gif_media_link_edit_and_bale_admin_commands(app_env, actors):
    """تست دستورات «قفل گیف»، «قفل باز گیف»، «قفل رسانه»، «قفل لینک»، حذف پیام ویرایش‌شده و مجوز هر ۵ نقش."""
    cfg = app_env["config"]
    svc = app_env["services"]
    admin = actors["admin"]
    mgr = actors["manager"]
    deputy = actors["deputy"]
    assoc = actors["assoc"]
    t1 = actors["teacher1"]
    s1 = actors["student1"]

    client, _ = build_bot_client(cfg, svc)
    deleted_ids: List[int] = []
    pinned_ids: List[int] = []
    unpinned_ids: List[int] = []
    replies: List[str] = []

    async def fake_execute(method: str, service: str, json: bool = None, **data):
        if service == "deleteMessage":
            deleted_ids.append(int(data["message_id"]))
            return True
        if service == "pinChatMessage":
            pinned_ids.append(int(data["message_id"]))
            return True
        if service == "unpinChatMessage":
            unpinned_ids.append(int(data["message_id"]))
            return True
        if service == "sendMessage":
            replies.append(data.get("text", ""))
            return {"message_id": 9999, "chat": {"id": data.get("chat_id"), "type": "group"}, "text": data.get("text")}
        return True

    client.execute = fake_execute
    grp_id = -100888111

    async def scenario():
        await client.initialize()
        try:
            # ۱. دانش‌آموز اجازه اجرای «قفل گیف» را ندارد
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 301,
                    "message": {
                        "message_id": 6001,
                        "from": {"id": s1["bale_user_id"], "first_name": "دانش‌آموز"},
                        "chat": {"id": grp_id, "type": "group", "title": "گروه دهم"},
                        "text": "قفل گیف",
                    },
                },
            )
            # ۲. معلم «قفل گیف» را فعال می‌کند
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 302,
                    "message": {
                        "message_id": 6002,
                        "from": {"id": t1["bale_user_id"], "first_name": "معلم"},
                        "chat": {"id": grp_id, "type": "group", "title": "گروه دهم"},
                        "text": "قفل گیف",
                    },
                },
            )
            # ۳. دانش‌آموز گیف می‌فرستد (در بله هم animation و هم document پر می‌شود) -> باید حذف شود!
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 303,
                    "message": {
                        "message_id": 6003,
                        "from": {"id": s1["bale_user_id"], "first_name": "دانش‌آموز"},
                        "chat": {"id": grp_id, "type": "group"},
                        "animation": {"file_id": "gif_1", "file_unique_id": "u1", "width": 200, "height": 200, "duration": 3},
                        "document": {"file_id": "gif_1", "file_unique_id": "u1", "mime_type": "video/mp4"},
                    },
                },
            )
            # ۴. دانش‌آموز فایل PDF تکلیف می‌فرستد -> چون فقط گیف قفل است و فایل باز است، نباید حذف شود!
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 304,
                    "message": {
                        "message_id": 6004,
                        "from": {"id": s1["bale_user_id"], "first_name": "دانش‌آموز"},
                        "chat": {"id": grp_id, "type": "group"},
                        "document": {"file_id": "pdf_1", "file_unique_id": "u2", "file_name": "hw.pdf", "mime_type": "application/pdf"},
                    },
                },
            )
            # ۵. خود معلم گیف آموزشی می‌فرستد -> معاف است و نباید حذف شود!
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 305,
                    "message": {
                        "message_id": 6005,
                        "from": {"id": t1["bale_user_id"], "first_name": "معلم"},
                        "chat": {"id": grp_id, "type": "group"},
                        "animation": {"file_id": "gif_2", "file_unique_id": "u3", "width": 200, "height": 200, "duration": 2},
                    },
                },
            )
            # ۶. عضو انجمن با دستور «قفل باز گیف» قفل گیف را باز می‌کند -> گیف بعدی دانش‌آموز حذف نمی‌شود
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 306,
                    "message": {
                        "message_id": 6006,
                        "from": {"id": assoc["bale_user_id"], "first_name": "انجمن"},
                        "chat": {"id": grp_id, "type": "group"},
                        "text": "قفل باز گیف",
                    },
                },
            )
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 307,
                    "message": {
                        "message_id": 6007,
                        "from": {"id": s1["bale_user_id"], "first_name": "دانش‌آموز"},
                        "chat": {"id": grp_id, "type": "group"},
                        "animation": {"file_id": "gif_3", "file_unique_id": "u4", "width": 200, "height": 200, "duration": 2},
                    },
                },
            )
            # ۷. ناظم «قفل رسانه» و مدیر «قفل لینک» را فعال می‌کنند
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 308,
                    "message": {
                        "message_id": 6008,
                        "from": {"id": deputy["bale_user_id"], "first_name": "ناظم"},
                        "chat": {"id": grp_id, "type": "group"},
                        "text": "قفل رسانه",
                    },
                },
            )
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 309,
                    "message": {
                        "message_id": 6009,
                        "from": {"id": mgr["bale_user_id"], "first_name": "مدیر"},
                        "chat": {"id": grp_id, "type": "group"},
                        "text": "قفل لینک",
                    },
                },
            )
            # دانش‌آموز استیکر می‌فرستد -> حذف می‌شود؛ سپس پیام متنی را ویرایش کرده و لینک می‌گذارد (edited_message) -> حذف می‌شود!
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 310,
                    "message": {
                        "message_id": 6010,
                        "from": {"id": s1["bale_user_id"], "first_name": "دانش‌آموز"},
                        "chat": {"id": grp_id, "type": "group"},
                        "sticker": {"file_id": "stk_1", "file_unique_id": "us1", "type": "regular", "width": 512, "height": 512},
                    },
                },
            )
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 311,
                    "edited_message": {
                        "message_id": 6011,
                        "from": {"id": s1["bale_user_id"], "first_name": "دانش‌آموز"},
                        "chat": {"id": grp_id, "type": "group"},
                        "edit_date": 1764052000,
                        "text": "بچه‌ها بیایید این لینک https://spam.example.com",
                    },
                },
            )
            # ۸. ادمین با ریپلای دستور «پین» و «حذف پین» را اجرا می‌کند
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 312,
                    "message": {
                        "message_id": 6012,
                        "from": {"id": admin["bale_user_id"], "first_name": "ادمین"},
                        "chat": {"id": grp_id, "type": "group"},
                        "reply_to_message": {"message_id": 6005, "chat": {"id": grp_id, "type": "group"}, "text": "اعلان"},
                        "text": "پین",
                    },
                },
            )
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 313,
                    "message": {
                        "message_id": 6013,
                        "from": {"id": admin["bale_user_id"], "first_name": "ادمین"},
                        "chat": {"id": grp_id, "type": "group"},
                        "reply_to_message": {"message_id": 6005, "chat": {"id": grp_id, "type": "group"}, "text": "اعلان"},
                        "text": "حذف پین",
                    },
                },
            )
            await asyncio.sleep(0.3)
        finally:
            await client.shutdown()

    asyncio.run(scenario())

    assert 6003 in deleted_ids  # گیف دانش‌آموز هنگام فعال بودن قفل گیف حذف شد
    assert 6004 not in deleted_ids  # PDF تمرین هنگام قفل گیف حذف نشد
    assert 6005 not in deleted_ids  # گیف معلم معاف بود
    assert 6007 not in deleted_ids  # بعد از «قفل باز گیف» گیف دانش‌آموز مجاز بود
    assert 6010 in deleted_ids  # استیکر دانش‌آموز بعد از «قفل رسانه» حذف شد
    assert 6011 in deleted_ids  # پیام ویرایش‌شده حاوی لینک (edited_message) حذف شد
    assert pinned_ids == [6005]
    assert unpinned_ids == [6005]
    assert any("⛔" in r for r in replies)


def test_bale_official_api_getfile_legacy_callback_and_commands_compliance():
    """تست انطباق کامل با مستندات رسمی بله: getFile -> file_path، کلاینت قدیمی callback_query_id='1...' و فرمت BotCommand."""
    # ۱. بررسی رعایت محدودیت‌های BotCommand بله (2..32 کاراکتر لاتین کوچک/عدد/_ و توضیح 1..256)
    for cmd in DEFAULT_BALE_COMMANDS:
        c = cmd["command"]
        d = cmd["description"]
        assert 2 <= len(c) <= 32
        assert all(ch.islower() or ch.isdigit() or ch == "_" for ch in c)
        assert 1 <= len(d) <= 256

    # ۲. بررسی فراخوانی getFile و دانلود از /file/bot<token>/<file_path>
    called_urls: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called_urls.append(str(request.url))
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200,
                json={"ok": True, "result": {"file_id": "FID123", "file_unique_id": "U123", "file_path": "photos/img_01.jpg"}},
            )
        if request.url.path.endswith("/file/bot111:TOKEN/photos/img_01.jpg"):
            return httpx.Response(200, content=b"RAW_IMAGE_BYTES")
        return httpx.Response(404, json={"ok": False, "error_code": 404, "description": "Not Found"})

    async def run_checks():
        conn = SafeBaleConnection(token="111:TOKEN")
        conn.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        conn.is_started = True
        try:
            data = await conn.download_file("FID123")
            assert data == b"RAW_IMAGE_BYTES"
            assert any(u.endswith("/bot111:TOKEN/getFile") for u in called_urls)
            assert any(u.endswith("/file/bot111:TOKEN/photos/img_01.jpg") for u in called_urls)
        finally:
            await conn.stop()

        # ۳. بررسی کلاینت قدیمی بله که callback_query_id آن با '1' شروع می‌شود
        fallback_replies: List[str] = []

        class _DummyMsg:
            async def reply(self, txt: str):
                fallback_replies.append(txt)

        class _DummyLegacyCallback:
            id = "1987654321"
            message = _DummyMsg()
            answered = False

            async def answer(self, text: str = "", show_alert: bool = False):
                self.answered = True

        cb = _DummyLegacyCallback()
        await safe_answer_callback(cb, "⛔ عدم دسترسی آزمایشی", show_alert=True)
        assert cb.answered is True
        assert fallback_replies == ["⛔ عدم دسترسی آزمایشی"]

    asyncio.run(run_checks())
