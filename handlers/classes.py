from __future__ import annotations

from balethon import Client
from balethon.conditions import equals, private
from balethon.objects import CallbackQuery, InlineKeyboard, InlineKeyboardButton, Message

from keyboards.admin import BTN_CLASSES_MGMT
from keyboards.main import confirm_inline, wizard_cancel_keyboard
from keyboards.manager import classes_mgmt_inline
from keyboards.teacher import BTN_TEACHER_CLASSES
from utils.helpers import safe_answer_callback
from utils.validators import ValidationError

from . import BotContext, cb_prefix

FLOW_CLASS = "CLASS_MGMT_FLOW"


def _class_detail_inline(cls: dict) -> InlineKeyboard:
    toggle_lbl = "⚪ غیرفعال‌سازی کلاس" if cls["is_active"] else "🟢 فعال‌سازی کلاس"
    toggle_val = "0" if cls["is_active"] else "1"
    return InlineKeyboard(
        [
            InlineKeyboardButton("👨‍🎓 افزودن دانش‌آموز", callback_data=f"cls:enroll:{cls['id']}"),
            InlineKeyboardButton("📋 فهرست دانش‌آموزان", callback_data=f"cls:students:{cls['id']}"),
        ],
        [
            InlineKeyboardButton("✏️ ویرایش نام کلاس", callback_data=f"cls:edit:{cls['id']}"),
            InlineKeyboardButton(toggle_lbl, callback_data=f"cls:toggle:{cls['id']}:{toggle_val}"),
        ],
        [
            InlineKeyboardButton("🗑 حذف کنترل‌شده کلاس", callback_data=f"cls:ask_del:{cls['id']}"),
            InlineKeyboardButton("📜 تاریخچه تغییرات کلاس", callback_data=f"cls:hist:{cls['id']}"),
        ],
    )


def register(client: Client, ctx: BotContext) -> None:
    @client.on_message(private & equals(BTN_CLASSES_MGMT))
    async def classes_menu(*, client: Client, message: Message):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "MANAGE_CLASSES", "CLASS")
            classes = ctx.services.timetable.classes.list_classes()
            await message.reply("🏫 مدیریت کلاس‌های مدرسه:", reply_markup=classes_mgmt_inline(classes))

        await ctx.run_safe_message(client, message, _run)

    @client.on_message(private & equals(BTN_TEACHER_CLASSES))
    async def teacher_classes_view(*, client: Client, message: Message):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "VIEW_OWN_CLASSES", "CLASS")
            courses = ctx.services.timetable.subjects.list_teacher_subjects(actor["id"])
            if not courses:
                await message.reply("هنوز هیچ کلاس یا درسی به شما تخصیص داده نشده است.")
                return
            lines = ["🏫 کلاس‌ها و درس‌های تخصیص‌یافته به شما:", "━" * 24]
            for cs in courses:
                lines.append(
                    f"▫️ کلاس {cs['class_name']} (پایه {cs['grade_level']}) ← درس «{cs['subject_name']}» "
                    f"({cs['weekly_periods']} زنگ در هفته | ضریب {cs['coefficient']:g})"
                )
            await message.reply("\n".join(lines))

        await ctx.run_safe_message(client, message, _run)

    @client.on_callback_query(cb_prefix("cls:"))
    async def class_callbacks(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "MANAGE_CLASSES", "CLASS")
            parts = callback_query.data.split(":")
            action = parts[1]
            uid = int(callback_query.author.id)
            chat_id = callback_query.message.chat.id

            if action == "create":
                ctx.users_repo.set_session(uid, FLOW_CLASS, "CREATE_NAME", {})
                await safe_answer_callback(callback_query, "ایجاد کلاس جدید")
                await client.send_message(chat_id, "🏫 نام کلاس جدید را وارد کنید (مثال: دهم-الف):", reply_markup=wizard_cancel_keyboard())
            elif action == "transfer":
                ctx.users_repo.set_session(uid, FLOW_CLASS, "TRANSFER_STUDENT_CODE", {})
                await safe_answer_callback(callback_query, "انتقال دانش‌آموز")
                await client.send_message(
                    chat_id,
                    "🔄 کد دانش‌آموزی یا شناسه بله دانش‌آموز موردنظر برای انتقال را وارد کنید:",
                    reply_markup=wizard_cancel_keyboard(),
                )
            elif action == "view":
                cid = int(parts[2])
                cls = ctx.services.timetable.classes.get_by_id(cid)
                if not cls:
                    raise ValidationError("کلاس یافت نشد.")
                subjects = ctx.services.timetable.subjects.list_class_subjects(cid)
                subj_txt = "، ".join(f"{s['subject_name']} ({s.get('teacher_name') or 'بدون دبیر'})" for s in subjects) or "ثبت‌نشده"
                await safe_answer_callback(callback_query, "جزئیات کلاس")
                await client.send_message(
                    chat_id,
                    f"🏫 کلاس: {cls['name']}\n"
                    f"▫️ پایه: {cls['grade_level']} | سال تحصیلی: {cls['academic_year']}\n"
                    f"▫️ ظرفیت: {cls['capacity']} | دانش‌آموزان فعال: {cls['active_student_count']}\n"
                    f"▫️ وضعیت: {'فعال 🟢' if cls['is_active'] else 'غیرفعال ⚪'}\n"
                    f"▫️ دروس و دبیران: {subj_txt}",
                    reply_markup=_class_detail_inline(cls),
                )
            elif action == "students":
                cid = int(parts[2])
                students = ctx.services.timetable.classes.list_class_students(cid, active_users_only=True)
                await safe_answer_callback(callback_query, f"{len(students)} دانش‌آموز")
                lines = [f"👨‍🎓 فهرست دانش‌آموزان فعال کلاس ({len(students)} نفر):"]
                for idx, s in enumerate(students, 1):
                    lines.append(f"{idx}. {s['full_name']} (کد: {s.get('student_code') or '-'} | شناسه: {s['bale_user_id']})")
                if not students:
                    lines.append("دانش‌آموز فعالی در این کلاس ثبت نشده است.")
                await client.send_message(chat_id, "\n".join(lines))
            elif action == "enroll":
                cid = int(parts[2])
                ctx.users_repo.set_session(uid, FLOW_CLASS, "ENROLL_STUDENT", {"class_id": cid})
                await safe_answer_callback(callback_query, "افزودن دانش‌آموز")
                await client.send_message(
                    chat_id,
                    "👨‍🎓 کد دانش‌آموزی یا شناسه بله دانش‌آموز را برای افزودن به این کلاس وارد کنید:",
                    reply_markup=wizard_cancel_keyboard(),
                )
            elif action == "edit":
                cid = int(parts[2])
                ctx.users_repo.set_session(uid, FLOW_CLASS, "EDIT_CLASS_NAME", {"class_id": cid})
                await safe_answer_callback(callback_query, "ویرایش نام کلاس")
                await client.send_message(chat_id, "✏️ نام جدید کلاس را وارد کنید:", reply_markup=wizard_cancel_keyboard())
            elif action == "toggle":
                cid, flag = int(parts[2]), int(parts[3])
                updated = ctx.services.timetable.update_class(actor, cid, is_active=bool(flag))
                await safe_answer_callback(callback_query, "وضعیت کلاس تغییر یافت.")
                await callback_query.message.edit_text(
                    f"✅ وضعیت کلاس «{updated['name']}» به {'فعال' if updated['is_active'] else 'غیرفعال'} تغییر کرد."
                )
            elif action == "ask_del":
                cid = int(parts[2])
                cls = ctx.services.timetable.classes.get_by_id(cid)
                if not cls:
                    raise ValidationError("کلاس یافت نشد.")
                await safe_answer_callback(callback_query, "تأیید حذف کلاس")
                await client.send_message(
                    chat_id,
                    f"⚠️ آیا از حذف کنترل‌شده کلاس «{cls['name']}» مطمئن هستید؟\n"
                    "(در صورت وجود سوابق آموزشی، کلاس برای حفاظت از تاریخچه بایگانی/غیرفعال می‌شود.)",
                    reply_markup=confirm_inline(f"cls:do_del:{cid}"),
                )
            elif action == "do_del":
                cid = int(parts[2])
                res = ctx.services.timetable.delete_class_controlled(actor, cid)
                await safe_answer_callback(callback_query, "انجام شد.")
                msg = (
                    "✅ کلاس دارای سوابق تاریخی بود و برای حفاظت کامل از سوابق آموزشی بایگانی/غیرفعال شد."
                    if res["mode"] == "ARCHIVED"
                    else "✅ کلاس فاقد سابقه آموزشی بود و به‌طور کامل حذف شد."
                )
                await callback_query.message.edit_text(msg)
            elif action == "hist":
                cid = int(parts[2])
                hist = ctx.services.timetable.classes.get_history(class_id=cid, limit=15)
                lines = ["📜 تاریخچه تغییرات کلاس:"]
                for h in hist:
                    lines.append(f"• {h['action']} | {h.get('note') or '-'} ({h.get('actor_name') or 'سیستم'})")
                await safe_answer_callback(callback_query, "تاریخچه کلاس")
                await client.send_message(chat_id, "\n".join(lines))

        await ctx.run_safe_callback(client, callback_query, _run, sensitive=True)

    ctx.register_session_flow(FLOW_CLASS, _handle_class_step)


async def _handle_class_step(ctx: BotContext, client: Client, message: Message, session: dict) -> None:
    uid = int(message.author.id)
    actor = ctx.services.permissions.require_permission(ctx.get_actor(uid), "MANAGE_CLASSES", "CLASS")
    step = session["step"]
    data = session["data"]
    raw = (message.text or "").strip()

    if step == "CREATE_NAME":
        data["name"] = raw
        ctx.users_repo.set_session(uid, FLOW_CLASS, "CREATE_GRADE", data)
        await message.reply("🎓 پایه تحصیلی کلاس را وارد کنید (مثال: 10):")
        return

    if step == "CREATE_GRADE":
        cls = ctx.services.timetable.create_class(actor, data["name"], raw)
        ctx.users_repo.clear_session(uid)
        await message.reply(
            f"✅ کلاس «{cls['name']}» (پایه {cls['grade_level']} - سال {cls['academic_year']}) ایجاد شد.",
            reply_markup=ctx.main_markup_for(actor),
        )
        return

    if step == "EDIT_CLASS_NAME":
        updated = ctx.services.timetable.update_class(actor, int(data["class_id"]), name=raw)
        ctx.users_repo.clear_session(uid)
        await message.reply(f"✅ نام کلاس به «{updated['name']}» تغییر یافت.", reply_markup=ctx.main_markup_for(actor))
        return

    if step == "ENROLL_STUDENT":
        student = ctx.users_repo.get_by_student_code(raw)
        if not student and raw.isdigit():
            student = ctx.users_repo.get_by_bale_id(int(raw))
        if not student:
            raise ValidationError("دانش‌آموزی با این کد یا شناسه یافت نشد.")
        ctx.services.timetable.enroll_student_in_class(actor, int(data["class_id"]), student["id"])
        ctx.users_repo.clear_session(uid)
        await message.reply(
            f"✅ دانش‌آموز «{student['full_name']}» به کلاس اضافه شد.",
            reply_markup=ctx.main_markup_for(actor),
        )
        return

    if step == "TRANSFER_STUDENT_CODE":
        student = ctx.users_repo.get_by_student_code(raw)
        if not student and raw.isdigit():
            student = ctx.users_repo.get_by_bale_id(int(raw))
        if not student or student["role"] != "STUDENT":
            raise ValidationError("دانش‌آموزی با این مشخصات یافت نشد.")
        data["student_id"] = student["id"]
        ctx.users_repo.set_session(uid, FLOW_CLASS, "TRANSFER_TARGET_CLASS", data)
        classes = ctx.services.timetable.classes.list_classes(active_only=True)
        cls_names = "، ".join(c["name"] for c in classes) or "-"
        await message.reply(f"🏫 نام کلاس مقصد برای «{student['full_name']}» را وارد کنید ({cls_names}):")
        return

    if step == "TRANSFER_TARGET_CLASS":
        year = ctx.services.notifications.settings.get("academic_year", "1405-1406")
        target_cls = ctx.services.timetable.classes.get_by_name_and_year(raw, year)
        if not target_cls:
            raise ValidationError("کلاس مقصد یافت نشد.")
        ctx.services.timetable.transfer_student_between_classes(actor, int(data["student_id"]), target_cls["id"])
        ctx.users_repo.clear_session(uid)
        await message.reply(
            f"✅ دانش‌آموز با موفقیت به کلاس «{target_cls['name']}» منتقل شد.",
            reply_markup=ctx.main_markup_for(actor),
        )
