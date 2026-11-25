from __future__ import annotations

import io
import re
from typing import Optional, Tuple

from balethon import Client
from balethon.conditions import Condition, equals, private
from balethon.objects import CallbackQuery, InlineKeyboard, InlineKeyboardButton, Message

from keyboards.admin import BTN_GRADES_MGMT
from keyboards.main import confirm_inline, wizard_cancel_keyboard
from keyboards.student import (
    BTN_PARENT_GRADES,
    BTN_STUDENT_GRADES,
    BTN_STUDENT_HOMEWORK,
    children_selector_inline,
    report_card_export_inline,
)
from keyboards.teacher import BTN_TEACHER_GRADES, BTN_TEACHER_HOMEWORK
from services.grade_service import AmbiguousTeacherSubjectError
from utils.dates import format_jalali_date, to_persian_digits
from utils.formatters import format_report_card_text
from utils.helpers import safe_answer_callback, send_chunked_message
from utils.security import generate_signed_report_token
from utils.validators import ValidationError

from . import BotContext, cb_prefix

FLOW_GRADES = "GRADES_FLOW"

_POS_MOD_RE = re.compile(r"^(?:/?مثبت(?:[_\s]+درسی)?|\+[1۱](?:\s*درسی)?)(?:\s*(?:\||\s)\s*(.+))?$")
_NEG_MOD_RE = re.compile(r"^(?:/?منفی(?:[_\s]+درسی)?|-[1۱](?:\s*درسی)?)(?:\s*(?:\||\s)\s*(.+))?$")


def _is_private_chat(msg: Optional[Message]) -> bool:
    if not msg or not msg.chat or msg.chat.type is None:
        return False
    val = getattr(msg.chat.type, "value", msg.chat.type)
    return str(val).lower().endswith("private")


def _is_forwarded_message(msg: Optional[Message]) -> bool:
    if not msg:
        return False
    return bool(
        getattr(msg, "forward_from", None)
        or getattr(msg, "forward_date", None)
        or getattr(msg, "forward_sender_name", None)
        or getattr(msg, "forward_origin", None)
    )


def _resolve_student_from_forwarded(ctx: BotContext, msg: Message) -> dict:
    """استخراج دانش‌آموز هدف از پیام فورواردشده در پی‌وی یا گروه."""
    fwd_user = getattr(msg, "forward_from", None)
    fwd_uid: Optional[int] = int(fwd_user.id) if (fwd_user and getattr(fwd_user, "id", None) is not None) else None

    if fwd_uid is None:
        origin = getattr(msg, "forward_origin", None)
        if isinstance(origin, dict):
            sender_user = origin.get("sender_user") or {}
            if sender_user.get("id") is not None:
                fwd_uid = int(sender_user["id"])
        elif origin is not None and getattr(origin, "sender_user", None) is not None:
            su = getattr(origin, "sender_user")
            if getattr(su, "id", None) is not None:
                fwd_uid = int(su.id)

    if fwd_uid is not None:
        student = ctx.users_repo.get_by_bale_id(fwd_uid)
        if not student or student["role"] != "STUDENT" or not student["is_active"]:
            raise ValidationError("فرستنده اصلی این پیام فورواردشده به عنوان دانش‌آموز فعال در سامانه ثبت نشده است.")
        return student

    sender_name = (getattr(msg, "forward_sender_name", None) or "").strip()
    if sender_name:
        matches = [
            u
            for u in ctx.users_repo.search_users(query=sender_name, role="STUDENT", active_only=True)
            if u["full_name"].strip() == sender_name
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValidationError(
                f"حریم خصوصی فوروارد دانش‌آموز بسته است و بیش از یک دانش‌آموز با نام «{sender_name}» وجود دارد؛ لطفاً در گروه روی پیام او ریپلای کنید."
            )

    raise ValidationError(
        "شناسه دانش‌آموز در پیام فورواردشده توسط تنظیمات حریم خصوصی بله مخفی شده یا در سامانه ثبت‌نام نکرده است."
    )


def _parse_continuous_modifier_text(
    text: Optional[str], allow_short: bool = False
) -> Optional[Tuple[float, Optional[str], Optional[str]]]:
    """تشخیص دستور «مثبت درسی» (+1) یا «منفی درسی» (-1) همراه با نام درس یا توضیح اختیاری."""
    if not text:
        return None
    cleaned = text.strip()
    for pattern, delta in ((_POS_MOD_RE, 1.0), (_NEG_MOD_RE, -1.0)):
        # در چت عمومی فرم بدون اسلش حتماً شامل «درسی» است؛ اما بلافاصله پس از فوروارد پیام در پی‌وی، فرم کوتاه هم مجاز است
        if not allow_short and not cleaned.startswith("/") and "درسی" not in cleaned:
            continue
        m = pattern.match(cleaned)
        if m:
            tail = (m.group(1) or "").strip()
            if not tail:
                return delta, None, None
            if "|" in tail:
                sub_parts = [p.strip() for p in tail.split("|", 1)]
                return delta, (sub_parts[0] or None), (sub_parts[1] or None)
            return delta, tail, None
    return None


def _private_forwarded_student_condition() -> Condition:
    class _FwdCond(Condition):
        def __init__(self) -> None:
            super().__init__(can_process=Message)

        async def __call__(self, client, event: Message) -> bool:
            return _is_private_chat(event) and _is_forwarded_message(event)

    return _FwdCond()


def _continuous_modifier_condition() -> Condition:
    class _ModCond(Condition):
        def __init__(self) -> None:
            super().__init__(can_process=Message)

        async def __call__(self, client, event: Message) -> bool:
            if _is_forwarded_message(event):
                return False
            return _parse_continuous_modifier_text(event.text, allow_short=False) is not None

    return _ModCond()


def _format_modifier_result(res: dict) -> str:
    is_pos = res["delta"] > 0
    header = "➕ مثبت درسی (+۱ نمره مستمر) ثبت شد" if is_pos else "➖ منفی درسی (-۱ نمره مستمر) ثبت شد"
    old_txt = to_persian_digits(f"{res['old_score']:g}")
    new_txt = to_persian_digits(f"{res['new_score']:g}")
    max_txt = to_persian_digits(f"{res['max_score']:g}")
    pos_txt = to_persian_digits(res["positive_count"])
    neg_txt = to_persian_digits(res["negative_count"])
    return (
        f"{header}\n"
        f"👤 دانش‌آموز: {res['student']['full_name']}\n"
        f"🏫 کلاس: {res['class_name']} | 📘 درس: {res['subject_name']}\n"
        f"📌 نوع اعمال: {res['evaluation_name']} (فقط مستمر — بدون تغییر در پایانی)\n"
        f"⭐ نمره مستمر: {old_txt} ← {new_txt} از {max_txt}\n"
        f"📊 جمع امتیازات کلاسی این درس: +{pos_txt} / -{neg_txt}"
    )


async def _apply_modifier_and_respond(
    *,
    ctx: BotContext,
    client: Client,
    actor: dict,
    student: dict,
    delta: float,
    subject_hint: Optional[str],
    note: Optional[str],
    source_chat_id: Optional[int],
    source_message_id: Optional[int],
    reply_message: Message,
) -> None:
    try:
        res = ctx.services.grades.apply_continuous_modifier(
            actor=actor,
            student_user_id=student["id"],
            delta=delta,
            subject_hint=subject_hint,
            note=note,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
        )
        await reply_message.reply(_format_modifier_result(res))
        await ctx.flush_notifications(client)
    except AmbiguousTeacherSubjectError as amb:
        sign_code = "pos" if amb.delta > 0 else "neg"
        kb = InlineKeyboard()
        for cs in amb.subjects:
            btn_label = f"{'➕ مثبت' if amb.delta > 0 else '➖ منفی'} درسی: {cs['subject_name']}"
            kb.add_row(
                InlineKeyboardButton(
                    btn_label,
                    callback_data=f"cmod:{actor['bale_user_id']}:{amb.student['id']}:{cs['subject_id']}:{sign_code}:{source_message_id or 0}",
                )
            )
        await reply_message.reply(
            f"👨‍🏫 شما در کلاس «{amb.cls['name']}» بیش از یک درس تدریس می‌کنید؛ درس موردنظر برای اعمال روی نمره مستمر «{amb.student['full_name']}» را انتخاب کنید:",
            reply_markup=kb,
        )


def register(client: Client, ctx: BotContext) -> None:
    @client.on_message(_private_forwarded_student_condition())
    async def private_forwarded_student_handler(*, client: Client, message: Message):
        async def _run(actor):
            checked = ctx.services.permissions.require_active_user(actor)
            uid = int(message.author.id)
            if checked["role"] not in {"ADMIN", "SCHOOL_MANAGER", "DEPUTY", "TEACHER", "ASSOCIATION_MEMBER"}:
                raise ValidationError("فوروارد پیام دانش‌آموز برای ثبت مثبت/منفی درسی فقط ویژه کارکنان مدرسه (ادمین، مدیر، ناظم، دبیر و انجمن) است.")

            # اگر کادر آموزشی در وسط یک فرم دیگر (مثلاً ثبت تکلیف) است، در آن فرم تداخل ایجاد نکنیم
            sess = ctx.users_repo.get_session(uid)
            if sess and not (sess.get("flow") == FLOW_GRADES and sess.get("step") == "FORWARDED_MOD_TARGET"):
                await message.reply(
                    "⚠️ شما یک فرم باز در حال تکمیل دارید. ابتدا آن را کامل کنید یا «❌ لغو عملیات» را بزنید و سپس پیام دانش‌آموز را فوروارد کنید."
                )
                return

            student = _resolve_student_from_forwarded(ctx, message)
            ctx.users_repo.set_session(
                uid,
                FLOW_GRADES,
                "FORWARDED_MOD_TARGET",
                {"student_id": student["id"], "fwd_msg_id": int(message.id) if message.id else 0},
            )
            kb = InlineKeyboard(
                [
                    InlineKeyboardButton(
                        "➕ مثبت درسی (+۱ مستمر)",
                        callback_data=f"cmod_quick:{checked['bale_user_id']}:{student['id']}:pos:{message.id or 0}",
                        style="success",
                    ),
                    InlineKeyboardButton(
                        "➖ منفی درسی (-۱ مستمر)",
                        callback_data=f"cmod_quick:{checked['bale_user_id']}:{student['id']}:neg:{message.id or 0}",
                        style="danger",
                    ),
                ]
            )
            await message.reply(
                f"👤 پیام دانش‌آموز «{student['full_name']}» دریافت شد.\n"
                f"✍️ اکنون دستور «مثبت درسی» یا «منفی درسی» را ارسال کنید (یا از دکمه‌های سریع زیر بزنید):",
                reply_markup=kb,
            )

        await ctx.run_safe_message(client, message, _run)

    @client.on_message(_continuous_modifier_condition())
    async def continuous_modifier_reply_handler(*, client: Client, message: Message):
        async def _run(actor):
            parsed = _parse_continuous_modifier_text(message.text, allow_short=False)
            if not parsed:
                return
            delta, subject_hint, note = parsed
            uid = int(message.author.id)
            student: Optional[dict] = None

            if message.reply_to_message:
                if _is_forwarded_message(message.reply_to_message):
                    student = _resolve_student_from_forwarded(ctx, message.reply_to_message)
                elif message.reply_to_message.author:
                    target_bale_id = int(message.reply_to_message.author.id)
                    student = ctx.users_repo.get_by_bale_id(target_bale_id)
                    if not student or student["role"] != "STUDENT" or not student["is_active"]:
                        raise ValidationError("کاربری که روی پیام او ریپلای کرده‌اید به عنوان دانش‌آموز فعال در سامانه ثبت نشده است.")
                # اگر در پی‌وی هم سشنی از فوروارد باز بود، پاک شود
                if _is_private_chat(message):
                    sess = ctx.users_repo.get_session(uid)
                    if sess and sess.get("flow") == FLOW_GRADES and sess.get("step") == "FORWARDED_MOD_TARGET":
                        ctx.users_repo.clear_session(uid)
            elif _is_private_chat(message):
                sess = ctx.users_repo.get_session(uid)
                if sess and sess.get("flow") == FLOW_GRADES and sess.get("step") == "FORWARDED_MOD_TARGET":
                    stu_id = int(sess["data"]["student_id"])
                    student = ctx.users_repo.get_by_id(stu_id)
                    ctx.users_repo.clear_session(uid)

            if not student:
                await message.reply(
                    "⚠️ برای ثبت «مثبت درسی» یا «منفی درسی» یکی از دو روش زیر را انجام دهید:\n"
                    "۱) در گروه روی پیام دانش‌آموز ریپلای (Reply) کنید و دستور را بفرستید.\n"
                    "۲) پیام دانش‌آموز را به شخصی (پی‌وی) ربات فوروارد کنید و بلافاصله بعدش دستور را بفرستید."
                )
                return

            checked = ctx.services.permissions.require_active_user(actor)
            await _apply_modifier_and_respond(
                ctx=ctx,
                client=client,
                actor=checked,
                student=student,
                delta=delta,
                subject_hint=subject_hint,
                note=note,
                source_chat_id=int(message.chat.id) if message.chat else None,
                source_message_id=int(message.id) if message.id else None,
                reply_message=message,
            )

        await ctx.run_safe_message(client, message, _run)

    @client.on_callback_query(cb_prefix("cmod_quick:"))
    async def continuous_modifier_quick_callback(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            parts = callback_query.data.split(":")
            expected_bale_id = int(parts[1])
            student_id = int(parts[2])
            delta = 1.0 if parts[3] == "pos" else -1.0
            src_msg_id = int(parts[4]) if len(parts) > 4 and int(parts[4]) > 0 else None

            if int(callback_query.author.id) != expected_bale_id:
                await safe_answer_callback(callback_query, "⛔ این دکمه فقط مخصوص دبیر صادرکننده است.", show_alert=True)
                return

            ctx.users_repo.clear_session(expected_bale_id)
            student = ctx.users_repo.get_by_id(student_id)
            if not student:
                raise ValidationError("دانش‌آموز یافت نشد.")

            try:
                res = ctx.services.grades.apply_continuous_modifier(
                    actor=actor,
                    student_user_id=student_id,
                    delta=delta,
                    source_chat_id=int(callback_query.message.chat.id) if (callback_query.message and src_msg_id) else None,
                    source_message_id=src_msg_id,
                )
                await safe_answer_callback(callback_query, "در نمره مستمر ثبت شد.")
                await callback_query.message.edit_text(_format_modifier_result(res))
                await ctx.flush_notifications(client)
            except AmbiguousTeacherSubjectError as amb:
                sign_code = "pos" if amb.delta > 0 else "neg"
                kb = InlineKeyboard()
                for cs in amb.subjects:
                    btn_label = f"{'➕ مثبت' if amb.delta > 0 else '➖ منفی'} درسی: {cs['subject_name']}"
                    kb.add_row(
                        InlineKeyboardButton(
                            btn_label,
                            callback_data=f"cmod:{actor['bale_user_id']}:{amb.student['id']}:{cs['subject_id']}:{sign_code}:{src_msg_id or 0}",
                        )
                    )
                await safe_answer_callback(callback_query, "درس را انتخاب کنید.")
                await callback_query.message.edit_text(
                    f"👨‍🏫 درس موردنظر برای اعمال روی نمره مستمر «{amb.student['full_name']}» را انتخاب کنید:",
                    reply_markup=kb,
                )

        await ctx.run_safe_callback(client, callback_query, _run, sensitive=True)

    @client.on_callback_query(cb_prefix("cmod:"))
    async def continuous_modifier_subject_callback(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            parts = callback_query.data.split(":")
            expected_bale_id = int(parts[1])
            student_id = int(parts[2])
            subject_id = int(parts[3])
            delta = 1.0 if parts[4] == "pos" else -1.0
            src_msg_id = int(parts[5]) if len(parts) > 5 and int(parts[5]) > 0 else None

            if int(callback_query.author.id) != expected_bale_id:
                await safe_answer_callback(callback_query, "⛔ این دکمه فقط مخصوص دبیر صادرکننده دستور است.", show_alert=True)
                return

            ctx.users_repo.clear_session(expected_bale_id)
            res = ctx.services.grades.apply_continuous_modifier(
                actor=actor,
                student_user_id=student_id,
                delta=delta,
                subject_id=subject_id,
                source_chat_id=int(callback_query.message.chat.id) if src_msg_id else None,
                source_message_id=src_msg_id,
            )
            await safe_answer_callback(callback_query, "در نمره مستمر ثبت شد.")
            await callback_query.message.edit_text(_format_modifier_result(res))
            await ctx.flush_notifications(client)

        await ctx.run_safe_callback(client, callback_query, _run, sensitive=True)

    @client.on_command(name="کارنامه")
    async def report_card_cmd(*_args, client: Client, message: Message):
        await _open_grades_view(client, message, ctx)

    @client.on_command(name="card")
    async def report_card_latin_cmd(*_args, client: Client, message: Message):
        await _open_grades_view(client, message, ctx)

    @client.on_message(private & equals(BTN_STUDENT_GRADES, BTN_PARENT_GRADES, BTN_TEACHER_GRADES, BTN_GRADES_MGMT))
    async def grades_buttons(*, client: Client, message: Message):
        await _open_grades_view(client, message, ctx)

    @client.on_message(private & equals(BTN_STUDENT_HOMEWORK, BTN_TEACHER_HOMEWORK))
    async def homework_buttons(*, client: Client, message: Message):
        async def _run(actor):
            checked = ctx.services.permissions.require_active_user(actor)
            if checked["role"] == "STUDENT":
                cls = ctx.services.timetable.classes.get_student_active_class(checked["id"])
                if not cls:
                    await message.reply("شما هنوز در کلاس فعالی ثبت نشده‌اید.")
                    return
                items = ctx.services.grades.list_class_homework(checked, cls["id"])
                lines = [f"📚 تکالیف فعال کلاس {cls['name']}:"]
                for hw in items:
                    due = format_jalali_date(hw["due_date"]) if hw.get("due_date") else "بدون مهلت"
                    lines.append(f"• {hw['subject_name']} - {hw['title']} (مهلت: {due})\n  {hw['description']}")
                if len(lines) == 1:
                    lines.append("تکلیفی ثبت نشده است.")
                await send_chunked_message(client, message.chat.id, "\n".join(lines))
            elif checked["role"] in {"TEACHER", "ADMIN", "SCHOOL_MANAGER"}:
                ctx.users_repo.set_session(int(message.author.id), FLOW_GRADES, "CREATE_HOMEWORK", {})
                await message.reply(
                    "📝 ثبت تکلیف جدید با جداکننده «|»:\n"
                    "نام کلاس | نام درس | عنوان تکلیف | توضیحات | تاریخ تحویل (اختیاری ۱۴۰۵/۰۸/۱۰)",
                    reply_markup=wizard_cancel_keyboard(),
                )

        await ctx.run_safe_message(client, message, _run)

    @client.on_callback_query(cb_prefix("grd:"))
    async def grade_callbacks(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            parts = callback_query.data.split(":")
            action = parts[1]
            uid = int(callback_query.author.id)
            chat_id = callback_query.message.chat.id

            if action == "child":
                child_id = int(parts[2])
                rc = ctx.services.grades.get_student_report_card(actor, child_id)
                await safe_answer_callback(callback_query, "کارنامه فرزند")
                await send_chunked_message(client, chat_id, format_report_card_text(rc))
                await client.send_message(chat_id, "دریافت نسخه رسمی کارنامه:", reply_markup=report_card_export_inline(child_id))
            elif action in {"rc_pdf", "rc_xlsx"}:
                stu_id = int(parts[2])
                fmt = "pdf" if action == "rc_pdf" else "xlsx"
                file_bytes = ctx.services.grades.export_student_report_card(actor, stu_id, fmt=fmt)
                bio = io.BytesIO(file_bytes)
                bio.name = f"report_card_{stu_id}.{fmt}"
                await safe_answer_callback(callback_query, f"فایل {fmt.upper()} کارنامه آماده شد.")
                await client.send_document(chat_id, bio, caption=f"🎓 کارنامه رسمی دانش‌آموز ({fmt.upper()})")
            elif action == "refresh":
                stu_id = int(parts[2])
                rc = ctx.services.grades.get_student_report_card(actor, stu_id)
                await safe_answer_callback(callback_query, "کارنامه بروزرسانی شد.")
                await send_chunked_message(client, chat_id, format_report_card_text(rc))
            elif action == "web_link":
                stu_id = int(parts[2])
                ctx.services.permissions.ensure_can_access_student(actor, stu_id)
                if not ctx.web_secret_key:
                    raise ValidationError("کلید امنیتی پنل وب تنظیم نشده است.")
                token = generate_signed_report_token(ctx.web_secret_key, stu_id, ttl_seconds=86400)
                base_url = ctx.services.notifications.settings.get("public_web_url", "http://127.0.0.1:5000").rstrip("/")
                link = f"{base_url}/online-card/{token}"
                link_kb = InlineKeyboard()
                if link.startswith("https://"):
                    link_kb.add_row(InlineKeyboardButton("🌐 باز کردن کارنامه آنلاین", url=link, style="success"))
                if 1 <= len(link) <= 256:
                    link_kb.add_row(InlineKeyboardButton("📋 رونوشت (کپی) لینک کارنامه آنلاین", copy_text=link, style="primary"))
                await safe_answer_callback(callback_query, "لینک کارنامه آنلاین ساخته شد.")
                await client.send_message(
                    chat_id,
                    f"🌐 لینک اختصاصی کارنامه آنلاین (معتبر به مدت ۲۴ ساعت):\n{link}\n\n"
                    f"💡 همچنین هر زمان شبانه‌روز می‌توانید از آدرس {base_url}/student-portal با کد دانش‌آموزی/شناسه بله و رمز کارنامه آنلاین خود وارد شوید.",
                    reply_markup=link_kb if link_kb.inline_keyboard else None,
                )
            elif action == "set_portal_pw":
                checked = ctx.services.permissions.require_active_user(actor)
                ctx.users_repo.set_session(uid, FLOW_GRADES, "SET_PORTAL_PW", {})
                await safe_answer_callback(callback_query, "تنظیم رمز کارنامه آنلاین")
                await client.send_message(
                    chat_id,
                    f"🔑 یک رمز عبور جدید (حداقل ۸ کاراکتر) برای ورود ۲۴ ساعته به درگاه کارنامه آنلاین وب ارسال کنید:\n"
                    f"(شناسه ورود شما: کد دانش‌آموزی {checked.get('student_code') or '-'} یا شناسه بله {checked['bale_user_id']})",
                    reply_markup=wizard_cancel_keyboard(),
                )
            elif action == "record":
                evals = ctx.services.grades.grades.list_evaluation_types()
                ev_txt = "\n".join(f"• شناسه {e['id']}: {e['name']} ({e['min_score']} تا {e['max_score']})" for e in evals)
                ctx.users_repo.set_session(uid, FLOW_GRADES, "RECORD_GRADE", {})
                await safe_answer_callback(callback_query, "ثبت نمره جدید")
                await client.send_message(
                    chat_id,
                    f"📊 انواع ارزیابی فعال:\n{ev_txt}\n\n"
                    "برای ثبت نمره به قالب زیر ارسال کنید:\n"
                    "نام کلاس | نام درس | کد دانش‌آموزی | شناسه ارزیابی | نمره",
                    reply_markup=wizard_cancel_keyboard(),
                )
            elif action == "edit":
                ctx.users_repo.set_session(uid, FLOW_GRADES, "EDIT_GRADE", {})
                await safe_answer_callback(callback_query, "ویرایش نمره")
                await client.send_message(
                    chat_id,
                    "✏️ برای اصلاح نمره ارسال کنید:\nشناسه نمره | نمره جدید | علت تغییر",
                    reply_markup=wizard_cancel_keyboard(),
                )
            elif action == "del":
                ctx.users_repo.set_session(uid, FLOW_GRADES, "ASK_DELETE_GRADE", {})
                await safe_answer_callback(callback_query, "حذف کنترل‌شده نمره")
                await client.send_message(
                    chat_id,
                    "🗑 شناسه نمره و علت حذف را وارد کنید:\nشناسه نمره | علت حذف",
                    reply_markup=wizard_cancel_keyboard(),
                )
            elif action == "do_del":
                gid = int(parts[2])
                ctx.services.grades.delete_grade_controlled(actor, gid, reason="تأیید حذف در ربات")
                await safe_answer_callback(callback_query, "نمره حذف شد.")
                await callback_query.message.edit_text(f"✅ نمره شماره #{gid} به‌صورت کنترل‌شده حذف و در تاریخچه ثبت شد.")
            elif action == "new_eval":
                ctx.services.permissions.require_permission(actor, "MANAGE_EVALUATION_TYPES", "EVALUATION_TYPE")
                ctx.users_repo.set_session(uid, FLOW_GRADES, "CREATE_EVAL", {})
                await safe_answer_callback(callback_query, "تعریف نوع ارزیابی")
                await client.send_message(
                    chat_id,
                    "➕ نوع ارزیابی جدید را وارد کنید:\nعنوان | دوره | کف نمره | سقف نمره | ضریب وزن\n"
                    "نمونه: کوییز مهر | ترم اول | 0 | 20 | 1",
                    reply_markup=wizard_cancel_keyboard(),
                )
            elif action == "history":
                ctx.services.permissions.require_roles(actor, "ADMIN", "SCHOOL_MANAGER")
                hist = ctx.services.grades.grades.list_history(limit=15)
                lines = ["📜 تاریخچه تغییرات نمرات:"]
                for h in hist:
                    lines.append(
                        f"• [{h['action']}] {h['student_name']} - {h['subject_name']}: "
                        f"{h['old_score']} -> {h['new_score']} ({h['actor_name']})"
                    )
                if len(lines) == 1:
                    lines.append("تغییری ثبت نشده است.")
                await safe_answer_callback(callback_query, "تاریخچه نمرات")
                await send_chunked_message(client, chat_id, "\n".join(lines))

        await ctx.run_safe_callback(client, callback_query, _run, sensitive=True)

    ctx.register_session_flow(FLOW_GRADES, _handle_grades_step)


async def _open_grades_view(client: Client, message: Message, ctx: BotContext) -> None:
    async def _run(actor):
        checked = ctx.services.permissions.require_active_user(actor)
        role = checked["role"].upper()
        if role == "STUDENT":
            rc = ctx.services.grades.get_student_report_card(checked, checked["id"])
            await send_chunked_message(client, message.chat.id, format_report_card_text(rc))
            await client.send_message(message.chat.id, "دریافت نسخه رسمی کارنامه:", reply_markup=report_card_export_inline(checked["id"]))
        elif role == "PARENT":
            children = ctx.users_repo.get_children_of_parent(checked["id"])
            if not children:
                await message.reply("فرزندی به حساب شما متصل نشده است.")
                return
            await message.reply("فرزند موردنظر را انتخاب کنید:", reply_markup=children_selector_inline(children, "grd:child"))
        elif role in {"TEACHER", "ADMIN", "SCHOOL_MANAGER"}:
            kb = InlineKeyboard(
                [
                    InlineKeyboardButton("➕ ثبت نمره جدید", callback_data="grd:record"),
                    InlineKeyboardButton("✏️ ویرایش نمره", callback_data="grd:edit"),
                ],
                [
                    InlineKeyboardButton("🗑 حذف کنترل‌شده نمره", callback_data="grd:del"),
                ],
            )
            if role in {"ADMIN", "SCHOOL_MANAGER"}:
                kb.add_row(
                    InlineKeyboardButton("⚙️ تعریف نوع ارزیابی", callback_data="grd:new_eval"),
                    InlineKeyboardButton("📜 تاریخچه تغییر نمرات", callback_data="grd:history"),
                )
            await message.reply("📊 سامانه مدیریت نمرات و کارنامه:", reply_markup=kb)
        else:
            await message.reply("دسترسی به بخش نمرات برای نقش شما مجاز نیست.")

    await ctx.run_safe_message(client, message, _run)


async def _handle_grades_step(ctx: BotContext, client: Client, message: Message, session: dict) -> None:
    uid = int(message.author.id)
    actor = ctx.services.permissions.require_active_user(ctx.get_actor(uid))
    step = session["step"]
    raw = (message.text or "").strip()

    if step == "RECORD_GRADE":
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) != 5 or not parts[3].isdigit():
            raise ValidationError("الگوی صحیح: نام کلاس | نام درس | کد دانش‌آموزی | شناسه ارزیابی | نمره")
        cls_name, subj_name, stu_ref, ev_id_raw, score_raw = parts
        year = ctx.services.notifications.settings.get("academic_year", "1405-1406")
        cls = ctx.services.timetable.classes.get_by_name_and_year(cls_name, year)
        if not cls:
            raise ValidationError("کلاس یافت نشد.")
        subs = [s for s in ctx.services.timetable.subjects.list_subjects(active_only=True) if s["name"] == subj_name]
        if not subs:
            raise ValidationError("درس یافت نشد.")
        student = ctx.users_repo.get_by_student_code(stu_ref)
        if not student and stu_ref.isdigit():
            student = ctx.users_repo.get_by_bale_id(int(stu_ref))
        if not student:
            raise ValidationError("دانش‌آموز یافت نشد.")
        grade = ctx.services.grades.record_grade(
            actor=actor,
            student_user_id=student["id"],
            class_id=cls["id"],
            subject_id=subs[0]["id"],
            evaluation_type_id=int(ev_id_raw),
            score=score_raw,
        )
        ctx.users_repo.clear_session(uid)
        await message.reply(
            f"✅ نمره #{grade['id']} ثبت شد: {student['full_name']} ← {grade['score']} در درس {subs[0]['name']}",
            reply_markup=ctx.main_markup_for(actor),
        )
        await ctx.flush_notifications(client)
        return

    if step == "EDIT_GRADE":
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) != 3 or not parts[0].isdigit():
            raise ValidationError("الگوی صحیح: شناسه نمره | نمره جدید | علت تغییر")
        updated = ctx.services.grades.update_grade(actor, int(parts[0]), parts[1], reason=parts[2])
        ctx.users_repo.clear_session(uid)
        await message.reply(
            f"✅ نمره #{updated['id']} به {updated['score']} اصلاح شد.",
            reply_markup=ctx.main_markup_for(actor),
        )
        await ctx.flush_notifications(client)
        return

    if step == "ASK_DELETE_GRADE":
        parts = [p.strip() for p in raw.split("|")]
        if not parts or not parts[0].isdigit():
            raise ValidationError("شناسه نمره باید عددی باشد.")
        gid = int(parts[0])
        ctx.users_repo.clear_session(uid)
        await message.reply(
            f"⚠️ آیا از حذف کنترل‌شده نمره شماره #{gid} اطمینان دارید؟",
            reply_markup=confirm_inline(f"grd:do_del:{gid}"),
        )
        return

    if step == "CREATE_EVAL":
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) != 5:
            raise ValidationError("الگوی صحیح: عنوان | دوره | کف نمره | سقف نمره | ضریب وزن")
        ev = ctx.services.grades.create_evaluation_type(
            actor, parts[0], parts[1], float(parts[2]), float(parts[3]), float(parts[4])
        )
        ctx.users_repo.clear_session(uid)
        await message.reply(f"✅ نوع ارزیابی «{ev['name']}» تعریف شد.", reply_markup=ctx.main_markup_for(actor))
        return

    if step == "CREATE_HOMEWORK":
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) not in (4, 5):
            raise ValidationError("الگوی صحیح: نام کلاس | نام درس | عنوان | توضیحات | تاریخ تحویل (اختیاری)")
        year = ctx.services.notifications.settings.get("academic_year", "1405-1406")
        cls = ctx.services.timetable.classes.get_by_name_and_year(parts[0], year)
        if not cls:
            raise ValidationError("کلاس یافت نشد.")
        subs = [s for s in ctx.services.timetable.subjects.list_subjects(active_only=True) if s["name"] == parts[1]]
        if not subs:
            raise ValidationError("درس یافت نشد.")
        due = parts[4] if len(parts) == 5 and parts[4] else None
        hw = ctx.services.grades.create_homework(actor, cls["id"], subs[0]["id"], parts[2], parts[3], due_date=due)
        ctx.users_repo.clear_session(uid)
        await message.reply(f"✅ تکلیف «{hw['title']}» ثبت شد.", reply_markup=ctx.main_markup_for(actor))
        return

    if step == "SET_PORTAL_PW":
        ctx.services.registration.set_web_password(actor, actor["id"], raw)
        ctx.users_repo.clear_session(uid)
        base_url = ctx.services.notifications.settings.get("public_web_url", "http://127.0.0.1:5000").rstrip("/")
        await message.reply(
            f"✅ رمز کارنامه آنلاین شما با موفقیت تنظیم شد.\n"
            f"اکنون در هر ساعت شبانه‌روز می‌توانید از آدرس زیر کارنامه آنلاین خود را مشاهده و چاپ کنید:\n"
            f"{base_url}/student-portal",
            reply_markup=ctx.main_markup_for(actor),
        )
        return

    if step == "FORWARDED_MOD_TARGET":
        parsed = _parse_continuous_modifier_text(raw, allow_short=True)
        if not parsed:
            raise ValidationError("پس از فوروارد پیام دانش‌آموز، عبارت «مثبت درسی» یا «منفی درسی» را ارسال کنید (یا دکمه لغو را بزنید).")
        delta, subject_hint, note = parsed
        stu_id = int(session["data"]["student_id"])
        student = ctx.users_repo.get_by_id(stu_id)
        ctx.users_repo.clear_session(uid)
        if not student or not student.get("is_active"):
            raise ValidationError("دانش‌آموز هدف یافت نشد.")
        await _apply_modifier_and_respond(
            ctx=ctx,
            client=client,
            actor=actor,
            student=student,
            delta=delta,
            subject_hint=subject_hint,
            note=note,
            source_chat_id=int(message.chat.id) if message.chat else None,
            source_message_id=int(message.id) if message.id else None,
            reply_message=message,
        )
