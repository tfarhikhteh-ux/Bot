from __future__ import annotations

from datetime import timedelta

from balethon import Client
from balethon.conditions import equals, private
from balethon.objects import CallbackQuery, InlineKeyboard, InlineKeyboardButton, Message

from keyboards.admin import BTN_ATTENDANCE_MGMT
from keyboards.main import wizard_cancel_keyboard
from keyboards.manager import BTN_DISCIPLINARY
from keyboards.student import BTN_PARENT_ATTENDANCE, BTN_STUDENT_ATTENDANCE, children_selector_inline
from keyboards.teacher import BTN_TEACHER_ATTENDANCE, attendance_status_inline, teacher_classes_inline
from utils.dates import format_jalali_date, today_date, today_iso
from utils.formatters import attendance_label
from utils.helpers import safe_answer_callback, send_chunked_message
from utils.validators import ValidationError

from . import BotContext, cb_prefix

FLOW_ATTENDANCE = "ATTENDANCE_FLOW"


def _format_student_attendance(student_name: str, report: dict) -> str:
    s = report["summary"]
    lines = [
        f"📋 کارکرد حضور و غیاب: {student_name}",
        "━" * 24,
        f"✅ حاضر: {s['PRESENT']} | ❌ غایب: {s['ABSENT']} | ⏰ تأخیر: {s['LATE']}",
        f"🚪 خروج زودهنگام: {s['EARLY_LEAVE']} | 📄 موجه: {s['EXCUSED_ABSENT']} | ⛔ غیرموجه: {s['UNEXCUSED_ABSENT']}",
        "━" * 24,
    ]
    for rec in report["records"][:15]:
        lines.append(
            f"• {format_jalali_date(rec['att_date'])} (زنگ {rec['period_number']}): {attendance_label(rec['status'])}"
            + (f" - {rec['note']}" if rec.get("note") else "")
        )
    if not report["records"]:
        lines.append("تاکنون رکوردی ثبت نشده است.")
    return "\n".join(lines)


def register(client: Client, ctx: BotContext) -> None:
    @client.on_command(name="حضور")
    async def attendance_cmd(*_args, client: Client, message: Message):
        await _route_attendance_entry(client, message, ctx)

    @client.on_message(private & equals(BTN_ATTENDANCE_MGMT, BTN_TEACHER_ATTENDANCE, BTN_STUDENT_ATTENDANCE, BTN_PARENT_ATTENDANCE))
    async def attendance_buttons(*, client: Client, message: Message):
        await _route_attendance_entry(client, message, ctx)

    @client.on_message(private & equals(BTN_DISCIPLINARY))
    async def disciplinary_menu(*, client: Client, message: Message):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "RECORD_DISCIPLINARY", "DISCIPLINARY")
            kb = InlineKeyboard(
                [
                    InlineKeyboardButton("➕ ثبت مورد انضباطی جدید", callback_data="att:disc_new"),
                    InlineKeyboardButton("📜 مشاهده موارد اخیر", callback_data="att:disc_list"),
                ]
            )
            await message.reply("⚖️ مدیریت پرونده انضباطی دانش‌آموزان:", reply_markup=kb)

        await ctx.run_safe_message(client, message, _run)

    @client.on_callback_query(cb_prefix("att:"))
    async def attendance_callbacks(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            parts = callback_query.data.split(":")
            action = parts[1]
            uid = int(callback_query.author.id)
            chat_id = callback_query.message.chat.id

            if action == "child":
                child_id = int(parts[2])
                child = ctx.users_repo.get_by_id(child_id)
                rep = ctx.services.attendance.get_student_attendance_report(actor, child_id)
                await safe_answer_callback(callback_query, "گزارش حضور و غیاب")
                await send_chunked_message(client, chat_id, _format_student_attendance(child["full_name"], rep))
            elif action == "pick_cls":
                cid = int(parts[2])
                ctx.services.permissions.ensure_can_access_class(actor, cid)
                kb = InlineKeyboard()
                for p in ctx.services.timetable.timetable.list_bell_periods():
                    kb.add_row(
                        InlineKeyboardButton(
                            f"🔔 زنگ {p['period_number']} ({p['start_time']}-{p['end_time']})",
                            callback_data=f"att:period:{cid}:{p['period_number']}",
                        )
                    )
                kb.add_row(InlineKeyboardButton("📊 گزارش حضور و غیاب این کلاس", callback_data=f"att:cls_rep:{cid}"))
                await safe_answer_callback(callback_query, "انتخاب زنگ")
                await client.send_message(chat_id, "زنگ موردنظر برای ثبت حضور و غیاب امروز را انتخاب کنید:", reply_markup=kb)
            elif action == "period":
                cid, p_num = int(parts[2]), int(parts[3])
                students = ctx.services.timetable.classes.list_class_students(cid, active_users_only=True)
                await safe_answer_callback(callback_query, f"{len(students)} دانش‌آموز")
                if not students:
                    await client.send_message(chat_id, "دانش‌آموز فعالی در این کلاس وجود ندارد.")
                    return
                for stu in students:
                    await client.send_message(
                        chat_id,
                        f"👤 دانش‌آموز: {stu['full_name']} (زنگ {p_num} - امروز)",
                        reply_markup=attendance_status_inline(cid, stu["id"], p_num),
                    )
            elif action == "set":
                cid, stu_id, p_num, st = int(parts[2]), int(parts[3]), int(parts[4]), parts[5]
                rec = ctx.services.attendance.record_attendance(
                    actor=actor,
                    class_id=cid,
                    student_user_id=stu_id,
                    status=st,
                    period_number=p_num,
                )
                await safe_answer_callback(callback_query, "ثبت شد.")
                await callback_query.message.edit_text(
                    f"✅ حضور و غیاب #{rec['id']} ثبت شد: {rec['student_name']} ← {attendance_label(st)}"
                )
                await ctx.flush_notifications(client)
            elif action == "cls_rep":
                cid = int(parts[2])
                rep = ctx.services.attendance.get_class_attendance_report(actor, cid)
                s = rep["summary"]
                await safe_answer_callback(callback_query, "گزارش کلاس")
                await client.send_message(
                    chat_id,
                    f"📊 خلاصه حضور و غیاب کلاس:\n"
                    f"کل رکوردها: {s['TOTAL']} | حاضر: {s['PRESENT']} | غایب: {s['ABSENT']} | تأخیر: {s['LATE']}\n"
                    f"غیبت موجه: {s['EXCUSED_ABSENT']} | غیبت غیرموجه: {s['UNEXCUSED_ABSENT']}",
                )
            elif action == "edit_wizard":
                ctx.users_repo.set_session(uid, FLOW_ATTENDANCE, "EDIT_RECORD", {})
                await safe_answer_callback(callback_query, "اصلاح حضور و غیاب")
                await client.send_message(
                    chat_id,
                    "✏️ برای اصلاح وضعیت حضور و غیاب به قالب زیر ارسال کنید:\n"
                    "شناسه رکورد | وضعیت جدید | علت اصلاح\n"
                    "(وضعیت‌ها: PRESENT, ABSENT, LATE, EARLY_LEAVE, EXCUSED_ABSENT, UNEXCUSED_ABSENT)",
                    reply_markup=wizard_cancel_keyboard(),
                )
            elif action in {"rep_daily", "rep_monthly", "rep_yearly"}:
                end_d = today_date()
                if action == "rep_daily":
                    start_d = end_d
                    label = "امروز"
                elif action == "rep_monthly":
                    start_d = end_d - timedelta(days=30)
                    label = "۳۰ روز اخیر"
                else:
                    start_d = end_d - timedelta(days=365)
                    label = "یک سال اخیر"
                rep = ctx.services.attendance.get_period_summary_report(actor, start_d.isoformat(), end_d.isoformat())
                s = rep["summary"]
                await safe_answer_callback(callback_query, f"گزارش {label}")
                await client.send_message(
                    chat_id,
                    f"📈 گزارش حضور و غیاب ({label}):\n"
                    f"▫️ مجموع ثبت‌شده: {s['TOTAL']}\n"
                    f"▫️ حاضر: {s['PRESENT']} | غایب: {s['ABSENT']} | تأخیر: {s['LATE']}\n"
                    f"▫️ خروج زودهنگام: {s['EARLY_LEAVE']} | موجه: {s['EXCUSED_ABSENT']} | غیرموجه: {s['UNEXCUSED_ABSENT']}",
                )
            elif action == "disc_new":
                ctx.services.permissions.require_permission(actor, "RECORD_DISCIPLINARY", "DISCIPLINARY")
                ctx.users_repo.set_session(uid, FLOW_ATTENDANCE, "DISC_CREATE", {})
                await safe_answer_callback(callback_query, "ثبت گزارش انضباطی")
                await client.send_message(
                    chat_id,
                    "⚖️ اطلاعات گزارش انضباطی را با «|» ارسال کنید:\n"
                    "کد دانش‌آموزی | دسته‌بندی | شرح کامل\n"
                    "نمونه: 1001 | تأخیر مکرر | سه جلسه تأخیر در هفته جاری",
                    reply_markup=wizard_cancel_keyboard(),
                )
            elif action == "disc_list":
                ctx.services.permissions.require_permission(actor, "VIEW_STUDENTS_STATUS", "DISCIPLINARY")
                items = ctx.services.attendance.attendance.list_disciplinary_notes(limit=15)
                lines = ["⚖️ گزارش‌های انضباطی اخیر:"]
                for it in items:
                    lines.append(
                        f"• {format_jalali_date(it['note_date'])} | {it['student_name']} ({it['category']}): {it['description']}"
                    )
                if len(lines) == 1:
                    lines.append("موردی ثبت نشده است.")
                await safe_answer_callback(callback_query, "فهرست انضباطی")
                await send_chunked_message(client, chat_id, "\n".join(lines))

        await ctx.run_safe_callback(client, callback_query, _run, sensitive=True)

    ctx.register_session_flow(FLOW_ATTENDANCE, _handle_attendance_step)


async def _route_attendance_entry(client: Client, message: Message, ctx: BotContext) -> None:
    async def _run(actor):
        checked = ctx.services.permissions.require_active_user(actor)
        role = checked["role"].upper()
        if role == "STUDENT":
            rep = ctx.services.attendance.get_student_attendance_report(checked, checked["id"])
            await send_chunked_message(client, message.chat.id, _format_student_attendance(checked["full_name"], rep))
        elif role == "PARENT":
            children = ctx.users_repo.get_children_of_parent(checked["id"])
            if not children:
                await message.reply("فرزندی به حساب کاربری شما متصل نشده است.")
                return
            await message.reply("فرزند موردنظر را انتخاب کنید:", reply_markup=children_selector_inline(children, "att:child"))
        elif role == "TEACHER":
            classes = ctx.services.timetable.classes.list_teacher_classes(checked["id"])
            if not classes:
                await message.reply("کلاسی به شما تخصیص داده نشده است.")
                return
            kb = teacher_classes_inline(classes, "att:pick_cls")
            kb.add_row(InlineKeyboardButton("✏️ اصلاح رکورد ثبت‌شده", callback_data="att:edit_wizard"))
            await message.reply("📋 کلاس موردنظر برای ثبت یا مشاهده حضور و غیاب را انتخاب کنید:", reply_markup=kb)
        elif role in {"ADMIN", "SCHOOL_MANAGER", "DEPUTY"}:
            classes = ctx.services.timetable.classes.list_classes(active_only=True)
            kb = teacher_classes_inline(classes[:8], "att:pick_cls")
            kb.add_row(
                InlineKeyboardButton("📆 گزارش روزانه", callback_data="att:rep_daily"),
                InlineKeyboardButton("📅 گزارش ماهانه", callback_data="att:rep_monthly"),
            )
            kb.add_row(
                InlineKeyboardButton("📊 گزارش سالانه", callback_data="att:rep_yearly"),
                InlineKeyboardButton("✏️ اصلاح با ذکر علت", callback_data="att:edit_wizard"),
            )
            await message.reply(f"📋 مدیریت حضور و غیاب ({format_jalali_date(today_iso())}):", reply_markup=kb)
        else:
            await message.reply("این بخش برای نقش کاربری شما فعال نیست.")

    await ctx.run_safe_message(client, message, _run)


async def _handle_attendance_step(ctx: BotContext, client: Client, message: Message, session: dict) -> None:
    uid = int(message.author.id)
    actor = ctx.services.permissions.require_active_user(ctx.get_actor(uid))
    step = session["step"]
    raw = (message.text or "").strip()

    if step == "EDIT_RECORD":
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) != 3 or not parts[0].isdigit():
            raise ValidationError("الگوی صحیح: شناسه رکورد | وضعیت جدید | علت اصلاح")
        updated = ctx.services.attendance.update_attendance(actor, int(parts[0]), parts[1], parts[2])
        ctx.users_repo.clear_session(uid)
        await message.reply(
            f"✅ رکورد #{updated['id']} برای «{updated['student_name']}» به {attendance_label(updated['status'])} اصلاح شد.",
            reply_markup=ctx.main_markup_for(actor),
        )
        await ctx.flush_notifications(client)
        return

    if step == "DISC_CREATE":
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) != 3:
            raise ValidationError("الگوی صحیح: کد دانش‌آموزی | دسته‌بندی | شرح گزارش")
        student = ctx.users_repo.get_by_student_code(parts[0])
        if not student and parts[0].isdigit():
            student = ctx.users_repo.get_by_bale_id(int(parts[0]))
        if not student:
            raise ValidationError("دانش‌آموز یافت نشد.")
        ctx.services.attendance.record_disciplinary_note(actor, student["id"], parts[1], parts[2])
        ctx.users_repo.clear_session(uid)
        await message.reply(
            f"✅ گزارش انضباطی برای «{student['full_name']}» ثبت شد.",
            reply_markup=ctx.main_markup_for(actor),
        )
