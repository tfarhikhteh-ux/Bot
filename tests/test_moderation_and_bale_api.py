from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import httpx

from main import build_bot_client
from utils.helpers import DEFAULT_BALE_COMMANDS, SafeBaleConnection, safe_answer_callback


def test_two_way_staff_commands_and_strict_teacher_only_modifiers(app_env, actors):
    """
    تست دقیق پاسخ‌های کاربر:
    ۱) «مثبت درسی» و «منفی درسی» منحصراً فقط برای معلمِ درس است (حتی ادمین، مدیر، ناظم و انجمن هم مسدودند).
    ۲) هر دو روش (ریپلای در گروه و فوروارد به شخصی ربات + دستور متنی یا دکمه سریع) برای تمام کارکنان
       متناسب با مجوز نقش خودشان کار می‌کند.
    """
    cfg = app_env["config"]
    svc = app_env["services"]
    admin = actors["admin"]
    mgr = actors["manager"]
    deputy = actors["deputy"]
    assoc = actors["assoc"]
    t1 = actors["teacher1"]
    s1 = actors["student1"]

    cls = svc.timetable.create_class(mgr, "دوازدهم-هدفمند", "12")
    subj = svc.timetable.create_subject(mgr, "ادبیات فارسی ۳", "12", "عمومی", default_coefficient=2.0)
    svc.timetable.assign_subject_to_class(mgr, cls["id"], subj["id"], t1["id"], coefficient=2.0)
    svc.timetable.enroll_student_in_class(mgr, cls["id"], s1["id"])

    evals = {e["name"]: e for e in svc.grades.grades.list_evaluation_types()}
    svc.grades.record_grade(t1, s1["id"], cls["id"], subj["id"], evals["ارزشیابی مستمر نوبت اول"]["id"], 16.0)
    final_row = svc.grades.record_grade(t1, s1["id"], cls["id"], subj["id"], evals["آزمون پایان‌ترم اول"]["id"], 19.5)

    # اعطای مجوز مشاهده گزارش به عضو انجمن برای تست مشاهده وضعیت پس از فوروارد
    svc.registration.grant_association_permissions(mgr, assoc["id"], ["VIEW_REPORTS_ALL"])

    client, _ = build_bot_client(cfg, svc)
    sent_messages: List[Dict[str, Any]] = []
    edited_messages: List[str] = []

    async def fake_execute(method: str, service: str, json: bool = None, **data):
        if service == "sendMessage":
            sent_messages.append(
                {
                    "chat_id": data.get("chat_id"),
                    "text": data.get("text", ""),
                    "reply_markup": data.get("reply_markup"),
                }
            )
            return {"message_id": len(sent_messages), "chat": {"id": data.get("chat_id"), "type": "group"}, "text": data.get("text")}
        if service == "editMessageText":
            edited_messages.append(data.get("text", ""))
            return True
        return True

    client.execute = fake_execute

    async def scenario():
        await client.initialize()
        try:
            # الف) تلاش ادمین و ناظم برای «مثبت درسی» با ریپلای در گروه -> هر دو باید با ⛔ مسدود شوند!
            for idx, non_teacher in enumerate((admin, mgr, deputy, assoc), start=1):
                await client.dispatcher.dispatch_raw_update(
                    client,
                    {
                        "update_id": 700 + idx,
                        "message": {
                            "message_id": 8000 + idx,
                            "from": {"id": non_teacher["bale_user_id"], "first_name": non_teacher["full_name"]},
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

            # ب) معلمِ درس با ریپلای در گروه «مثبت درسی» می‌دهد (+1 -> 17.0) و با دکمه سریع پس از فوروارد در پی‌وی «مثبت درسی» می‌دهد (+1 -> 18.0)
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 710,
                    "message": {
                        "message_id": 8010,
                        "from": {"id": t1["bale_user_id"], "first_name": t1["full_name"]},
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
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 711,
                    "message": {
                        "message_id": 8011,
                        "from": {"id": t1["bale_user_id"], "first_name": t1["full_name"]},
                        "chat": {"id": t1["bale_user_id"], "type": "private"},
                        "forward_from": {"id": s1["bale_user_id"], "first_name": s1["full_name"]},
                        "forward_date": 1764051000,
                        "text": "تمرین ارسالی دانش‌آموز",
                    },
                },
            )
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 712,
                    "callback_query": {
                        "id": "cb_t1_quick_pos",
                        "from": {"id": t1["bale_user_id"], "first_name": t1["full_name"]},
                        "message": {"message_id": 8012, "chat": {"id": t1["bale_user_id"], "type": "private"}, "text": "هدف"},
                        "data": f"stf:mod:{t1['bale_user_id']}:{s1['id']}:pos:8011",
                    },
                },
            )

            # ج) ناظم (DEPUTY): ۱) با ریپلای در گروه «غایب» ثبت می‌کند؛ ۲) با فوروارد پیام دانش‌آموز به پی‌وی ربات و تایپ «انضباطی تأخیر در صف صبحگاه» مورد انضباطی ثبت می‌کند!
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 720,
                    "message": {
                        "message_id": 8020,
                        "from": {"id": deputy["bale_user_id"], "first_name": deputy["full_name"]},
                        "chat": {"id": -100991, "type": "group"},
                        "reply_to_message": {
                            "message_id": 7999,
                            "from": {"id": s1["bale_user_id"], "first_name": s1["full_name"]},
                            "chat": {"id": -100991, "type": "group"},
                            "text": "سلام",
                        },
                        "text": "غایب",
                    },
                },
            )
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 721,
                    "message": {
                        "message_id": 8021,
                        "from": {"id": deputy["bale_user_id"], "first_name": deputy["full_name"]},
                        "chat": {"id": deputy["bale_user_id"], "type": "private"},
                        "forward_from": {"id": s1["bale_user_id"], "first_name": s1["full_name"]},
                        "forward_date": 1764051100,
                        "text": "پیام دانش‌آموز",
                    },
                },
            )
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 722,
                    "message": {
                        "message_id": 8022,
                        "from": {"id": deputy["bale_user_id"], "first_name": deputy["full_name"]},
                        "chat": {"id": deputy["bale_user_id"], "type": "private"},
                        "text": "انضباطی تأخیر در صف صبحگاه",
                    },
                },
            )

            # د) مدیر مدرسه و عضو انجمن: فوروارد به پی‌وی ربات و ارسال دستور «وضعیت»
            for idx, staff_u in enumerate((mgr, assoc), start=1):
                await client.dispatcher.dispatch_raw_update(
                    client,
                    {
                        "update_id": 730 + idx * 2,
                        "message": {
                            "message_id": 8030 + idx * 2,
                            "from": {"id": staff_u["bale_user_id"], "first_name": staff_u["full_name"]},
                            "chat": {"id": staff_u["bale_user_id"], "type": "private"},
                            "forward_from": {"id": s1["bale_user_id"], "first_name": s1["full_name"]},
                            "forward_date": 1764051200,
                            "text": "سوال دانش‌آموز",
                        },
                    },
                )
                await client.dispatcher.dispatch_raw_update(
                    client,
                    {
                        "update_id": 731 + idx * 2,
                        "message": {
                            "message_id": 8031 + idx * 2,
                            "from": {"id": staff_u["bale_user_id"], "first_name": staff_u["full_name"]},
                            "chat": {"id": staff_u["bale_user_id"], "type": "private"},
                            "text": "وضعیت",
                        },
                    },
                )
            await asyncio.sleep(0.3)
        finally:
            await client.shutdown()

    asyncio.run(scenario())

    # بررسی نمره مستمر (فقط ۲ مثبت معلم اعمال شده: 16 -> 18.0) و دست‌نخورده ماندن پایانی (19.5)
    rc = svc.grades.get_student_report_card(s1, s1["id"])
    subj_entry = rc["subjects"][0]
    assert subj_entry["continuous_score"] == 18.0
    assert subj_entry["final_score"] == 19.5
    assert subj_entry["positive_count"] == 2
    assert svc.grades.grades.get_by_id(final_row["id"])["score"] == 19.5
    assert any("مثبت درسی (+۱ نمره مستمر) ثبت شد" in e for e in edited_messages)

    # بررسی ۴ پیام رد دسترسی ⛔ برای ادمین، مدیر، ناظم و انجمن هنگام زدن «مثبت درسی»
    grp_msgs = [m["text"] for m in sent_messages if m["chat_id"] == -100991]
    assert sum(1 for t in grp_msgs if "منحصراً مخصوص معلم" in t) == 4

    # بررسی ثبت غیبت و مورد انضباطی توسط ناظم از طریق دو روش
    att_counts = svc.attendance.attendance.summarize_status_counts(student_user_id=s1["id"])
    assert att_counts.get("ABSENT", 0) == 1
    disc_notes = svc.attendance.attendance.list_disciplinary_notes(student_user_id=s1["id"])
    assert any("تأخیر در صف صبحگاه" in n["description"] for n in disc_notes)

    # بررسی دریافت دکمه‌های سریع نقش و گزارش وضعیت در پی‌وی مدیر و عضو انجمن
    mgr_pv = "\n".join(m["text"] for m in sent_messages if m["chat_id"] == mgr["bale_user_id"])
    assoc_pv = "\n".join(m["text"] for m in sent_messages if m["chat_id"] == assoc["bale_user_id"])
    assert "پرونده وضعیت کاربر" in mgr_pv
    assert "پرونده وضعیت کاربر" in assoc_pv


def test_group_locks_teacher_own_class_scope_and_bale_media_enforcement(app_env, actors):
    """
    تست دقیق محدوده دسترسی قفل گیف و رسانه:
    - ادمین، مدیر، ناظم و انجمن در همه گروه‌ها مجازند.
    - معلم فقط و فقط در گروهِ کلاسِ تخصیص‌یافته به خودش مجاز است (در گروه عمومی یا گروه کلاس دیگر مسدود می‌شود).
    """
    cfg = app_env["config"]
    svc = app_env["services"]
    admin = actors["admin"]
    mgr = actors["manager"]
    deputy = actors["deputy"]
    assoc = actors["assoc"]
    t1, t2 = actors["teacher1"], actors["teacher2"]
    s1 = actors["student1"]

    cls1 = svc.timetable.create_class(mgr, "دهم-الف", "10")
    cls2 = svc.timetable.create_class(mgr, "دهم-ب", "10")
    subj = svc.timetable.create_subject(mgr, "ریاضی ۱", "10", "تخصصی")
    svc.timetable.assign_subject_to_class(mgr, cls1["id"], subj["id"], t1["id"])
    svc.timetable.assign_subject_to_class(mgr, cls2["id"], subj["id"], t2["id"])

    client, _ = build_bot_client(cfg, svc)
    deleted_ids: List[int] = []
    pinned_ids: List[int] = []
    unpinned_ids: List[int] = []
    replies: List[Dict[str, Any]] = []

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
            replies.append({"chat_id": data.get("chat_id"), "text": data.get("text", "")})
            return {"message_id": 9999, "chat": {"id": data.get("chat_id"), "type": "group"}, "text": data.get("text")}
        return True

    client.execute = fake_execute
    grp_cls1 = -100888101
    grp_cls2 = -100888102

    async def scenario():
        await client.initialize()
        try:
            # ۱. قبل از اتصال گروه به کلاس، تلاش معلم ۱ برای «قفل گیف» باید رد شود (⛔)
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 401,
                    "message": {
                        "message_id": 6001,
                        "from": {"id": t1["bale_user_id"], "first_name": "معلم۱"},
                        "chat": {"id": grp_cls1, "type": "group", "title": "گروه آزمایشی"},
                        "text": "قفل گیف",
                    },
                },
            )
            # ۲. ناظم در گروه عمومی (بدون نیاز به اتصال کلاس) می‌تواند قفل باز/بسته کند و گروه اول را به «دهم-الف» و گروه دوم را به «دهم-ب» متصل کند
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 402,
                    "message": {
                        "message_id": 6002,
                        "from": {"id": deputy["bale_user_id"], "first_name": "ناظم"},
                        "chat": {"id": grp_cls1, "type": "group", "title": "گروه اول"},
                        "text": "اتصال کلاس دهم-الف",
                    },
                },
            )
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 403,
                    "message": {
                        "message_id": 6003,
                        "from": {"id": mgr["bale_user_id"], "first_name": "مدیر"},
                        "chat": {"id": grp_cls2, "type": "group", "title": "گروه دوم"},
                        "text": "اتصال کلاس دهم-ب",
                    },
                },
            )
            # ۳. اکنون معلم ۱ در گروه کلاس خودش (دهم-الف) «قفل گیف» می‌زند -> موفق! اما در گروه دهم-ب -> مسدود (⛔)!
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 404,
                    "message": {
                        "message_id": 6004,
                        "from": {"id": t1["bale_user_id"], "first_name": "معلم۱"},
                        "chat": {"id": grp_cls1, "type": "group"},
                        "text": "قفل گیف",
                    },
                },
            )
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 405,
                    "message": {
                        "message_id": 6005,
                        "from": {"id": t1["bale_user_id"], "first_name": "معلم۱"},
                        "chat": {"id": grp_cls2, "type": "group"},
                        "text": "قفل گیف",
                    },
                },
            )
            # ۴. در گروه دهم-الف: گیف دانش‌آموز (animation + document) حذف می‌شود، PDF تمرین حذف نمی‌شود، گیف معلم۱ حذف نمی‌شود، اما گیف معلم۲ (که دبیر این کلاس نیست!) حذف می‌شود!
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 406,
                    "message": {
                        "message_id": 6006,
                        "from": {"id": s1["bale_user_id"], "first_name": "دانش‌آموز"},
                        "chat": {"id": grp_cls1, "type": "group"},
                        "animation": {"file_id": "gif_1", "file_unique_id": "u1", "width": 200, "height": 200, "duration": 3},
                        "document": {"file_id": "gif_1", "file_unique_id": "u1", "mime_type": "video/mp4"},
                    },
                },
            )
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 407,
                    "message": {
                        "message_id": 6007,
                        "from": {"id": s1["bale_user_id"], "first_name": "دانش‌آموز"},
                        "chat": {"id": grp_cls1, "type": "group"},
                        "document": {"file_id": "pdf_1", "file_unique_id": "u2", "file_name": "hw.pdf", "mime_type": "application/pdf"},
                    },
                },
            )
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 408,
                    "message": {
                        "message_id": 6008,
                        "from": {"id": t1["bale_user_id"], "first_name": "معلم۱"},
                        "chat": {"id": grp_cls1, "type": "group"},
                        "animation": {"file_id": "gif_2", "file_unique_id": "u3", "width": 200, "height": 200, "duration": 2},
                    },
                },
            )
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 409,
                    "message": {
                        "message_id": 6009,
                        "from": {"id": t2["bale_user_id"], "first_name": "معلم۲"},
                        "chat": {"id": grp_cls1, "type": "group"},
                        "animation": {"file_id": "gif_3", "file_unique_id": "u4", "width": 200, "height": 200, "duration": 2},
                    },
                },
            )
            # ۵. عضو انجمن در هر گروهی (حتی گروه کلاس دهم-الف) می‌تواند «قفل باز گیف» و «قفل لینک» بزند
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 410,
                    "message": {
                        "message_id": 6010,
                        "from": {"id": assoc["bale_user_id"], "first_name": "انجمن"},
                        "chat": {"id": grp_cls1, "type": "group"},
                        "text": "قفل باز گیف",
                    },
                },
            )
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 411,
                    "message": {
                        "message_id": 6011,
                        "from": {"id": assoc["bale_user_id"], "first_name": "انجمن"},
                        "chat": {"id": grp_cls1, "type": "group"},
                        "text": "قفل لینک",
                    },
                },
            )
            # ویرایش پیام (edited_message) حاوی لینک توسط دانش‌آموز -> حذف فوری
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 412,
                    "edited_message": {
                        "message_id": 6012,
                        "from": {"id": s1["bale_user_id"], "first_name": "دانش‌آموز"},
                        "chat": {"id": grp_cls1, "type": "group"},
                        "edit_date": 1764052000,
                        "text": "لینک تبلیغاتی https://spam.example.com",
                    },
                },
            )
            # پین و حذف پین توسط ادمین
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 413,
                    "message": {
                        "message_id": 6013,
                        "from": {"id": admin["bale_user_id"], "first_name": "ادمین"},
                        "chat": {"id": grp_cls1, "type": "group"},
                        "reply_to_message": {"message_id": 6008, "chat": {"id": grp_cls1, "type": "group"}, "text": "اعلان"},
                        "text": "پین",
                    },
                },
            )
            await client.dispatcher.dispatch_raw_update(
                client,
                {
                    "update_id": 414,
                    "message": {
                        "message_id": 6014,
                        "from": {"id": admin["bale_user_id"], "first_name": "ادمین"},
                        "chat": {"id": grp_cls1, "type": "group"},
                        "reply_to_message": {"message_id": 6008, "chat": {"id": grp_cls1, "type": "group"}, "text": "اعلان"},
                        "text": "حذف پین",
                    },
                },
            )
            await asyncio.sleep(0.3)
        finally:
            await client.shutdown()

    asyncio.run(scenario())

    assert 6006 in deleted_ids  # گیف دانش‌آموز حذف شد
    assert 6007 not in deleted_ids  # PDF تمرین حذف نشد
    assert 6008 not in deleted_ids  # گیف معلمِ همان کلاس (t1) معاف بود
    assert 6009 in deleted_ids  # گیف معلمِ کلاس دیگر (t2) در گروه دهم-الف حذف شد!
    assert 6012 in deleted_ids  # پیام ویرایش‌شده حاوی لینک حذف شد
    assert pinned_ids == [6008]
    assert unpinned_ids == [6008]
    cls2_replies = "\n".join(r["text"] for r in replies if r["chat_id"] == grp_cls2)
    assert "⛔" in cls2_replies  # تلاش معلم۱ برای قفل در گروه دهم-ب مسدود شد


def test_bale_official_api_getfile_legacy_callback_and_commands_compliance():
    """تست انطباق کامل با مستندات رسمی بله: getFile -> file_path، کلاینت قدیمی callback_query_id='1...' و فرمت BotCommand."""
    for cmd in DEFAULT_BALE_COMMANDS:
        c = cmd["command"]
        d = cmd["description"]
        assert 2 <= len(c) <= 32
        assert all(ch.islower() or ch.isdigit() or ch == "_" for ch in c)
        assert 1 <= len(d) <= 256

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
