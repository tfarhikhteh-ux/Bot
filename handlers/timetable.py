from __future__ import annotations

import io

from balethon import Client
from balethon.conditions import equals, private
from balethon.objects import CallbackQuery, InlineKeyboard, InlineKeyboardButton, Message

from keyboards.admin import BTN_TIMETABLE_MGMT
from keyboards.main import wizard_cancel_keyboard
from keyboards.student import (
    BTN_PARENT_TIMETABLE,
    BTN_STUDENT_TIMETABLE,
    children_selector_inline,
    timetable_view_options_inline,
)
from keyboards.teacher import BTN_TEACHER_TIMETABLE
from utils.formatters import week_type_label
from utils.helpers import safe_answer_callback, send_chunked_message
from utils.validators import ValidationError

from . import BotContext, cb_prefix

FLOW_TIMETABLE = "TIMETABLE_MGMT_FLOW"


def register(client: Client, ctx: BotContext) -> None:
    @client.on_command(name="برنامه_ویرایش")
    async def timetable_quick_edit_cmd(*_args, client: Client, message: Message):
        async def _run(actor):
            parts = (message.text or "").split(maxsplit=1)
            arg_str = parts[1] if len(parts) > 1 else ""
            result_msg = ctx.services.timetable.apply_quick_edit_command(actor, arg_str)
            await message.reply(result_msg)

        await ctx.run_safe_message(client, message, _run)

    @client.on_command(name="برنامه")
    async def timetable_view_cmd(*_args, client: Client, message: Message):
        await _open_role_timetable(client, message, ctx)

    @client.on_message(private & equals(BTN_STUDENT_TIMETABLE, BTN_PARENT_TIMETABLE, BTN_TEACHER_TIMETABLE))
    async def role_timetable_btn(*, client: Client, message: Message):
        await _open_role_timetable(client, message, ctx)

    @client.on_message(private & equals(BTN_TIMETABLE_MGMT))
    async def timetable_mgmt_menu(*, client: Client, message: Message):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "MANAGE_TIMETABLE", "TIMETABLE")
            active_wk = ctx.services.notifications.settings.get_active_week_type()
            classes = ctx.services.timetable.classes.list_classes(active_only=True)
            kb = InlineKeyboard(
                [
                    InlineKeyboardButton("➕ ثبت / ویرایش خانه برنامه", callback_data="tt:set_wizard"),
                    InlineKeyboardButton("🧹 پاک‌کردن خانه برنامه", callback_data="tt:clear_wizard"),
                ],
                [
                    InlineKeyboardButton("🔔 تنظیم ساعات زنگ‌ها", callback_data="tt:bell_wizard"),
                    InlineKeyboardButton("⚠️ گزارش تعارض‌های برنامه", callback_data="tt:conflicts"),
                ],
            )
            for c in classes[:8]:
                kb.add_row(InlineKeyboardButton(f"📅 برنامه کلاس {c['name']}", callback_data=f"tt:options:{c['id']}"))
            await message.reply(
                f"📅 مدیریت برنامه هفتگی مدرسه (وضعیت هفته جاری: {week_type_label(active_wk)})\n"
                "همچنین می‌توانید از دستور سریع زیر استفاده کنید:\n"
                "/برنامه_ویرایش کلاس | روز | زنگ | هفته | درس",
                reply_markup=kb,
            )

        await ctx.run_safe_message(client, message, _run)

    @client.on_callback_query(cb_prefix("tt:"))
    async def timetable_callbacks(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            parts = callback_query.data.split(":")
            action = parts[1]
            uid = int(callback_query.author.id)
            chat_id = callback_query.message.chat.id

            if action == "child":
                child_id = int(parts[2])
                ctx.services.permissions.ensure_can_access_student(actor, child_id)
                cls = ctx.services.timetable.classes.get_student_active_class(child_id)
                if not cls:
                    raise ValidationError("فرزند شما هنوز در کلاس فعالی ثبت نشده است.")
                await safe_answer_callback(callback_query, f"کلاس {cls['name']}")
                await client.send_message(
                    chat_id,
                    f"📅 برنامه هفتگی کلاس «{cls['name']}» - نوع نمایش را انتخاب کنید:",
                    reply_markup=timetable_view_options_inline(cls["id"]),
                )
            elif action == "options":
                cid = int(parts[2])
                ctx.services.permissions.ensure_can_access_class(actor, cid)
                await safe_answer_callback(callback_query, "گزینه‌های نمایش")
                await client.send_message(
                    chat_id,
                    "📅 شیوه مشاهده یا دریافت خروجی برنامه کلاس را انتخاب کنید:",
                    reply_markup=timetable_view_options_inline(cid),
                )
            elif action == "cls":
                cid = int(parts[2])
                wt = None if parts[3] == "ALL" else parts[3]
                txt = ctx.services.timetable.get_formatted_class_timetable(actor, cid, week_type=wt, daily_only=False)
                await safe_answer_callback(callback_query, "برنامه بارگذاری شد.")
                await send_chunked_message(client, chat_id, txt)
            elif action == "day":
                cid = int(parts[2])
                active_wk = ctx.services.notifications.settings.get_active_week_type()
                txt = ctx.services.timetable.get_formatted_class_timetable(actor, cid, week_type=active_wk, daily_only=True)
                await safe_answer_callback(callback_query, "برنامه امروز")
                await send_chunked_message(client, chat_id, txt)
            elif action == "png":
                cid = int(parts[2])
                png_bytes = ctx.services.timetable.export_class_timetable_png(actor, cid)
                bio = io.BytesIO(png_bytes)
                bio.name = f"timetable_{cid}.png"
                await safe_answer_callback(callback_query, "تصویر برنامه آماده شد.")
                await client.send_photo(chat_id, bio, caption="🖼 جدول تصویری برنامه هفتگی کلاس")
            elif action == "pdf":
                cid = int(parts[2])
                pdf_bytes = ctx.services.timetable.export_class_timetable_pdf(actor, cid)
                bio = io.BytesIO(pdf_bytes)
                bio.name = f"timetable_{cid}.pdf"
                await safe_answer_callback(callback_query, "فایل PDF آماده شد.")
                await client.send_document(chat_id, bio, caption="📄 فایل PDF برنامه هفتگی کلاس")
            elif action == "xlsx":
                cid = int(parts[2])
                xlsx_bytes = ctx.services.timetable.export_class_timetable_xlsx(actor, cid)
                bio = io.BytesIO(xlsx_bytes)
                bio.name = f"timetable_{cid}.xlsx"
                await safe_answer_callback(callback_query, "فایل اکسل آماده شد.")
                await client.send_document(chat_id, bio, caption="📗 فایل اکسل برنامه هفتگی کلاس")
            elif action == "set_wizard":
                ctx.services.permissions.require_permission(actor, "MANAGE_TIMETABLE", "TIMETABLE")
                ctx.users_repo.set_session(uid, FLOW_TIMETABLE, "SET_QUICK", {})
                await safe_answer_callback(callback_query, "ثبت خانه برنامه")
                await client.send_message(
                    chat_id,
                    "📝 اطلاعات خانه برنامه را با جداکننده «|» ارسال کنید:\n"
                    "نام کلاس | روز | شماره زنگ | هفته (هر دو / هفته اول / هفته دوم) | نام درس\n\n"
                    "نمونه:\nدهم-الف | شنبه | 1 | هفته اول | ریاضی ۱",
                    reply_markup=wizard_cancel_keyboard(),
                )
            elif action == "clear_wizard":
                ctx.services.permissions.require_permission(actor, "MANAGE_TIMETABLE", "TIMETABLE")
                ctx.users_repo.set_session(uid, FLOW_TIMETABLE, "CLEAR_QUICK", {})
                await safe_answer_callback(callback_query, "پاک‌کردن خانه برنامه")
                await client.send_message(
                    chat_id,
                    "🧹 برای پاک‌کردن خانه برنامه به صورت زیر بفرستید:\n"
                    "نام کلاس | روز | شماره زنگ | نوع هفته\n"
                    "نمونه: دهم-الف | شنبه | 1 | هفته اول",
                    reply_markup=wizard_cancel_keyboard(),
                )
            elif action == "bell_wizard":
                ctx.services.permissions.require_permission(actor, "MANAGE_TIMETABLE", "BELL_PERIOD")
                bells = ctx.services.timetable.timetable.list_bell_periods()
                b_txt = "\n".join(f"• زنگ {b['period_number']} ({b['title']}): {b['start_time']} تا {b['end_time']}" for b in bells)
                ctx.users_repo.set_session(uid, FLOW_TIMETABLE, "SET_BELL", {})
                await safe_answer_callback(callback_query, "تنظیم زنگ‌ها")
                await client.send_message(
                    chat_id,
                    f"🔔 زنگ‌های فعلی:\n{b_txt}\n\n"
                    "برای افزودن یا ویرایش زنگ به قالب زیر ارسال کنید:\n"
                    "شماره زنگ | عنوان | ساعت شروع | ساعت پایان\n"
                    "نمونه: 1 | زنگ اول | 07:45 | 09:00",
                    reply_markup=wizard_cancel_keyboard(),
                )
            elif action == "conflicts":
                ctx.services.permissions.require_permission(actor, "MANAGE_TIMETABLE", "TIMETABLE")
                conflicts = ctx.services.timetable.timetable.detect_all_conflicts()
                await safe_answer_callback(callback_query, f"{len(conflicts)} تعارض")
                if not conflicts:
                    await client.send_message(chat_id, "✅ هیچ تعارضی در برنامه هفتگی کلاس‌ها و دبیران وجود ندارد.")
                else:
                    lines = [f"⚠️ تعداد {len(conflicts)} تعارض شناسایی شد:"]
                    for c in conflicts:
                        lines.append(str(c))
                    await send_chunked_message(client, chat_id, "\n".join(lines))

        await ctx.run_safe_callback(client, callback_query, _run)

    ctx.register_session_flow(FLOW_TIMETABLE, _handle_timetable_step)


async def _open_role_timetable(client: Client, message: Message, ctx: BotContext) -> None:
    async def _run(actor):
        checked = ctx.services.permissions.require_active_user(actor)
        role = checked["role"].upper()
        if role == "STUDENT":
            cls = ctx.services.timetable.classes.get_student_active_class(checked["id"])
            if not cls:
                await message.reply("شما هنوز در کلاس فعالی ثبت نشده‌اید.")
                return
            await message.reply(
                f"📅 برنامه هفتگی کلاس «{cls['name']}»:",
                reply_markup=timetable_view_options_inline(cls["id"]),
            )
        elif role == "PARENT":
            children = ctx.users_repo.get_children_of_parent(checked["id"])
            if not children:
                await message.reply("فرزندی به حساب شما متصل نشده است.")
                return
            await message.reply(
                "👨‍👩‍👧 فرزند موردنظر را برای مشاهده برنامه هفتگی انتخاب کنید:",
                reply_markup=children_selector_inline(children, "tt:child"),
            )
        elif role == "TEACHER":
            txt = ctx.services.timetable.get_formatted_teacher_timetable(checked)
            await send_chunked_message(client, message.chat.id, txt)
        else:
            classes = ctx.services.timetable.classes.list_classes(active_only=True)
            if not classes:
                await message.reply("هنوز کلاسی تعریف نشده است.")
                return
            kb = InlineKeyboard()
            for c in classes[:10]:
                kb.add_row(InlineKeyboardButton(f"📅 کلاس {c['name']}", callback_data=f"tt:options:{c['id']}"))
            await message.reply("کلاس موردنظر را انتخاب کنید:", reply_markup=kb)

    await ctx.run_safe_message(client, message, _run)


async def _handle_timetable_step(ctx: BotContext, client: Client, message: Message, session: dict) -> None:
    uid = int(message.author.id)
    actor = ctx.services.permissions.require_permission(ctx.get_actor(uid), "MANAGE_TIMETABLE", "TIMETABLE")
    step = session["step"]
    raw = (message.text or "").strip()

    if step == "SET_QUICK":
        res = ctx.services.timetable.apply_quick_edit_command(actor, raw)
        ctx.users_repo.clear_session(uid)
        await message.reply(res, reply_markup=ctx.main_markup_for(actor))
        return

    if step == "CLEAR_QUICK":
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) != 4:
            raise ValidationError("لطفاً ۴ بخش را با «|» جدا کنید: کلاس | روز | زنگ | هفته")
        res = ctx.services.timetable.apply_quick_edit_command(actor, f"{raw} | حذف")
        ctx.users_repo.clear_session(uid)
        await message.reply(res, reply_markup=ctx.main_markup_for(actor))
        return

    if step == "SET_BELL":
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) != 4:
            raise ValidationError("الگوی صحیح: شماره زنگ | عنوان | ساعت شروع | ساعت پایان")
        period = ctx.services.timetable.configure_bell_period(actor, int(parts[0]), parts[1], parts[2], parts[3])
        ctx.users_repo.clear_session(uid)
        await message.reply(
            f"✅ {period['title']} ({period['start_time']} تا {period['end_time']}) ذخیره شد.",
            reply_markup=ctx.main_markup_for(actor),
        )
