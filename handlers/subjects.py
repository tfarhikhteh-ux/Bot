from __future__ import annotations

from balethon import Client
from balethon.conditions import equals, private
from balethon.objects import CallbackQuery, InlineKeyboard, InlineKeyboardButton, Message

from keyboards.admin import BTN_SUBJECTS_MGMT
from keyboards.main import confirm_inline, wizard_cancel_keyboard
from keyboards.manager import subjects_mgmt_inline
from utils.helpers import safe_answer_callback
from utils.validators import ValidationError

from . import BotContext, cb_prefix

FLOW_SUBJECT = "SUBJECT_MGMT_FLOW"


def register(client: Client, ctx: BotContext) -> None:
    @client.on_message(private & equals(BTN_SUBJECTS_MGMT))
    async def subjects_menu(*, client: Client, message: Message):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "MANAGE_SUBJECTS", "SUBJECT")
            subjects = ctx.services.timetable.subjects.list_subjects(active_only=True)
            await message.reply("📚 مدیریت درس‌ها و تخصیص دبیر:", reply_markup=subjects_mgmt_inline(subjects))

        await ctx.run_safe_message(client, message, _run)

    @client.on_callback_query(cb_prefix("sub:"))
    async def subject_callbacks(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "MANAGE_SUBJECTS", "SUBJECT")
            parts = callback_query.data.split(":")
            action = parts[1]
            uid = int(callback_query.author.id)
            chat_id = callback_query.message.chat.id

            if action == "create":
                ctx.users_repo.set_session(uid, FLOW_SUBJECT, "CREATE_NAME", {})
                await safe_answer_callback(callback_query, "تعریف درس جدید")
                await client.send_message(chat_id, "📘 نام درس جدید را وارد کنید (مثال: فیزیک ۱):", reply_markup=wizard_cancel_keyboard())
            elif action == "assign":
                ctx.users_repo.set_session(uid, FLOW_SUBJECT, "ASSIGN_CLASS_NAME", {})
                await safe_answer_callback(callback_query, "تخصیص درس به کلاس")
                classes = ctx.services.timetable.classes.list_classes(active_only=True)
                names = "، ".join(c["name"] for c in classes) or "-"
                await client.send_message(chat_id, f"🏫 نام کلاس را وارد کنید ({names}):", reply_markup=wizard_cancel_keyboard())
            elif action == "view":
                sid = int(parts[2])
                subj = ctx.services.timetable.subjects.get_by_id(sid)
                if not subj:
                    raise ValidationError("درس یافت نشد.")
                kb = InlineKeyboard(
                    [
                        InlineKeyboardButton("✏️ ویرایش نام درس", callback_data=f"sub:edit:{sid}"),
                        InlineKeyboardButton("🗑 حذف کنترل‌شده", callback_data=f"sub:ask_del:{sid}"),
                    ]
                )
                await safe_answer_callback(callback_query, "مشخصات درس")
                await client.send_message(
                    chat_id,
                    f"📘 درس: {subj['name']}\n"
                    f"▫️ پایه: {subj['grade_level']} | نوع: {subj['subject_type']}\n"
                    f"▫️ تعداد زنگ پیش‌فرض هفتگی: {subj['default_weekly_periods']}\n"
                    f"▫️ ضریب پیش‌فرض: {subj['default_coefficient']:g}",
                    reply_markup=kb,
                )
            elif action == "edit":
                sid = int(parts[2])
                ctx.users_repo.set_session(uid, FLOW_SUBJECT, "EDIT_NAME", {"subject_id": sid})
                await safe_answer_callback(callback_query, "ویرایش نام درس")
                await client.send_message(chat_id, "✏️ نام جدید درس را وارد کنید:", reply_markup=wizard_cancel_keyboard())
            elif action == "ask_del":
                sid = int(parts[2])
                subj = ctx.services.timetable.subjects.get_by_id(sid)
                if not subj:
                    raise ValidationError("درس یافت نشد.")
                await safe_answer_callback(callback_query, "تأیید حذف")
                await client.send_message(
                    chat_id,
                    f"⚠️ آیا از حذف کنترل‌شده درس «{subj['name']}» اطمینان دارید؟",
                    reply_markup=confirm_inline(f"sub:do_del:{sid}"),
                )
            elif action == "do_del":
                sid = int(parts[2])
                mode = ctx.services.timetable.delete_subject_controlled(actor, sid)
                await safe_answer_callback(callback_query, "انجام شد.")
                await callback_query.message.edit_text(
                    "✅ درس به دلیل داشتن سابقه نمره/برنامه غیرفعال شد." if mode == "ARCHIVED" else "✅ درس حذف شد."
                )

        await ctx.run_safe_callback(client, callback_query, _run, sensitive=True)

    ctx.register_session_flow(FLOW_SUBJECT, _handle_subject_step)


async def _handle_subject_step(ctx: BotContext, client: Client, message: Message, session: dict) -> None:
    uid = int(message.author.id)
    actor = ctx.services.permissions.require_permission(ctx.get_actor(uid), "MANAGE_SUBJECTS", "SUBJECT")
    step = session["step"]
    data = session["data"]
    raw = (message.text or "").strip()

    if step == "CREATE_NAME":
        data["name"] = raw
        ctx.users_repo.set_session(uid, FLOW_SUBJECT, "CREATE_GRADE", data)
        await message.reply("🎓 پایه تحصیلی درس را وارد کنید (مثال: 10 یا عمومی):")
        return

    if step == "CREATE_GRADE":
        data["grade_level"] = raw
        ctx.users_repo.set_session(uid, FLOW_SUBJECT, "CREATE_TYPE", data)
        await message.reply("📂 نوع درس را وارد کنید (عمومی / تخصصی / کارگاهی / آزمایشگاه / فوق‌برنامه):")
        return

    if step == "CREATE_TYPE":
        data["subject_type"] = raw
        ctx.users_repo.set_session(uid, FLOW_SUBJECT, "CREATE_COEF", data)
        await message.reply("⚖️ ضریب درس و تعداد زنگ هفتگی را با فاصله وارد کنید (مثال: 3 4):")
        return

    if step == "CREATE_COEF":
        parts = raw.split()
        coef = float(parts[0]) if parts else 1.0
        periods = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 2
        subj = ctx.services.timetable.create_subject(
            actor=actor,
            name=data["name"],
            grade_level=data["grade_level"],
            subject_type=data["subject_type"],
            default_weekly_periods=periods,
            default_coefficient=coef,
        )
        ctx.users_repo.clear_session(uid)
        await message.reply(f"✅ درس «{subj['name']}» ثبت شد.", reply_markup=ctx.main_markup_for(actor))
        return

    if step == "EDIT_NAME":
        updated = ctx.services.timetable.update_subject(actor, int(data["subject_id"]), name=raw)
        ctx.users_repo.clear_session(uid)
        await message.reply(f"✅ نام درس به «{updated['name']}» تغییر یافت.", reply_markup=ctx.main_markup_for(actor))
        return

    if step == "ASSIGN_CLASS_NAME":
        year = ctx.services.notifications.settings.get("academic_year", "1405-1406")
        cls = ctx.services.timetable.classes.get_by_name_and_year(raw, year)
        if not cls:
            raise ValidationError("کلاس موردنظر یافت نشد.")
        data["class_id"] = cls["id"]
        ctx.users_repo.set_session(uid, FLOW_SUBJECT, "ASSIGN_SUBJECT_NAME", data)
        subs = ctx.services.timetable.subjects.list_subjects(active_only=True)
        await message.reply(f"📘 نام درس را وارد کنید ({'، '.join(s['name'] for s in subs) or '-'}):")
        return

    if step == "ASSIGN_SUBJECT_NAME":
        subs = [s for s in ctx.services.timetable.subjects.list_subjects(active_only=True) if s["name"] == raw]
        if not subs:
            raise ValidationError("درس موردنظر یافت نشد.")
        data["subject_id"] = subs[0]["id"]
        ctx.users_repo.set_session(uid, FLOW_SUBJECT, "ASSIGN_TEACHER", data)
        teachers = ctx.users_repo.search(role="TEACHER", active_only=True, limit=20)
        t_hint = "، ".join(f"{t['full_name']}({t['bale_user_id']})" for t in teachers) or "بدون دبیر"
        await message.reply(f"👨‍🏫 شناسه بله دبیر درس را وارد کنید (یا «ندارد» بفرستید):\n{t_hint}")
        return

    if step == "ASSIGN_TEACHER":
        teacher_id = None
        if raw not in {"ندارد", "-"}:
            if not raw.isdigit():
                raise ValidationError("شناسه بله دبیر باید عددی باشد.")
            t = ctx.users_repo.get_by_bale_id(int(raw))
            if not t or t["role"] != "TEACHER":
                raise ValidationError("دبیری با این شناسه یافت نشد.")
            teacher_id = t["id"]
        ctx.services.timetable.assign_subject_to_class(
            actor=actor,
            class_id=int(data["class_id"]),
            subject_id=int(data["subject_id"]),
            teacher_user_id=teacher_id,
        )
        ctx.users_repo.clear_session(uid)
        await message.reply("✅ درس و دبیر با موفقیت به کلاس تخصیص یافت.", reply_markup=ctx.main_markup_for(actor))
