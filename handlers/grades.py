from __future__ import annotations

from balethon import Client
from balethon.conditions import equals, private
from balethon.objects import CallbackQuery, InlineKeyboard, InlineKeyboardButton, Message

from keyboards.admin import BTN_GRADES_MGMT
from keyboards.main import confirm_inline, wizard_cancel_keyboard
from keyboards.student import (
    BTN_PARENT_GRADES,
    BTN_STUDENT_GRADES,
    BTN_STUDENT_HOMEWORK,
    children_selector_inline,
)
from keyboards.teacher import BTN_TEACHER_GRADES, BTN_TEACHER_HOMEWORK
from utils.dates import format_jalali_date
from utils.formatters import format_report_card_text
from utils.helpers import safe_answer_callback, send_chunked_message
from utils.validators import ValidationError

from . import BotContext, cb_prefix

FLOW_GRADES = "GRADES_FLOW"


def register(client: Client, ctx: BotContext) -> None:
    @client.on_command(name="کارنامه")
    async def report_card_cmd(*_args, client: Client, message: Message):
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
