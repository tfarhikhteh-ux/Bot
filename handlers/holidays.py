from __future__ import annotations

from balethon import Client
from balethon.conditions import equals, private
from balethon.objects import CallbackQuery, InlineKeyboard, InlineKeyboardButton, Message

from keyboards.admin import BTN_HOLIDAYS_MGMT
from keyboards.main import confirm_inline, wizard_cancel_keyboard
from keyboards.manager import BTN_VIEW_CALENDAR, holidays_mgmt_inline
from utils.dates import format_jalali_date
from utils.formatters import holiday_type_label
from utils.helpers import safe_answer_callback, send_chunked_message
from utils.validators import ValidationError

from . import BotContext, cb_prefix

FLOW_HOLIDAY = "HOLIDAY_FLOW"


def _format_calendar_text(ctx: BotContext) -> str:
    st = ctx.services.notifications.settings
    items = ctx.services.notifications.holidays.list_holidays(limit=25)
    lines = [
        f"🗓 تقویم آموزشی سال تحصیلی {st.get('academic_year', '1405-1406')} ({st.get('current_term', 'ترم اول')})",
        f"▫️ شروع ترم اول: {format_jalali_date(st.get('term1_start'))} | پایان: {format_jalali_date(st.get('term1_end'))}",
        f"▫️ شروع ترم دوم: {format_jalali_date(st.get('term2_start'))} | پایان: {format_jalali_date(st.get('term2_end'))}",
        "━" * 24,
    ]
    if not items:
        lines.append("مورد تعطیلی یا رویدادی ثبت نشده است.")
    for h in items:
        d_txt = format_jalali_date(h["holiday_date"])
        if h["end_date"] != h["holiday_date"]:
            d_txt += f" تا {format_jalali_date(h['end_date'])}"
        close_txt = " [تعطیل کامل]" if h["is_full_close"] else " [رویداد آموزشی]"
        lines.append(f"• {d_txt}: {h['title']} ({holiday_type_label(h['holiday_type'])}){close_txt}")
    return "\n".join(lines)


def register(client: Client, ctx: BotContext) -> None:
    @client.on_command(name="تقویم")
    async def calendar_cmd(*_args, client: Client, message: Message):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "VIEW_CALENDAR", "HOLIDAY")
            await send_chunked_message(client, message.chat.id, _format_calendar_text(ctx))

        await ctx.run_safe_message(client, message, _run)

    @client.on_message(private & equals(BTN_VIEW_CALENDAR))
    async def view_calendar_btn(*, client: Client, message: Message):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "VIEW_CALENDAR", "HOLIDAY")
            await send_chunked_message(client, message.chat.id, _format_calendar_text(ctx))

        await ctx.run_safe_message(client, message, _run)

    @client.on_message(private & equals(BTN_HOLIDAYS_MGMT))
    async def holidays_mgmt_btn(*, client: Client, message: Message):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "MANAGE_HOLIDAYS", "HOLIDAY")
            await message.reply("🗓 مدیریت تعطیلات و تقویم مدرسه:", reply_markup=holidays_mgmt_inline())

        await ctx.run_safe_message(client, message, _run)

    @client.on_callback_query(cb_prefix("hol:"))
    async def holiday_callbacks(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "MANAGE_HOLIDAYS", "HOLIDAY")
            parts = callback_query.data.split(":")
            action = parts[1]
            uid = int(callback_query.author.id)
            chat_id = callback_query.message.chat.id

            if action == "create":
                ctx.users_repo.set_session(uid, FLOW_HOLIDAY, "CREATE", {})
                await safe_answer_callback(callback_query, "ثبت تعطیلی جدید")
                await client.send_message(
                    chat_id,
                    "🗓 اطلاعات تعطیلی یا رویداد را با «|» ارسال کنید:\n"
                    "تاریخ (۱۴۰۵/۱۱/۲۲) | عنوان | نوع (OFFICIAL / SCHOOL / EMERGENCY / EVENT) | اطلاع‌رسانی فوری (بله/خیر)",
                    reply_markup=wizard_cancel_keyboard(),
                )
            elif action == "list":
                items = ctx.services.notifications.holidays.list_holidays(limit=15)
                await safe_answer_callback(callback_query, f"{len(items)} مورد")
                await send_chunked_message(client, chat_id, _format_calendar_text(ctx))
                if items:
                    kb = InlineKeyboard()
                    for h in items[:6]:
                        kb.add_row(InlineKeyboardButton(f"🗑 حذف «{h['title']}»", callback_data=f"hol:ask_del:{h['id']}"))
                    await client.send_message(chat_id, "برای حذف هر مورد تقویمی انتخاب کنید:", reply_markup=kb)
            elif action == "ask_del":
                hid = int(parts[2])
                await safe_answer_callback(callback_query, "تأیید حذف")
                await client.send_message(
                    chat_id,
                    f"⚠️ آیا از حذف مورد تقویمی #{hid} مطمئن هستید؟",
                    reply_markup=confirm_inline(f"hol:do_del:{hid}"),
                )
            elif action == "do_del":
                hid = int(parts[2])
                ctx.services.notifications.delete_holiday(actor, hid)
                await safe_answer_callback(callback_query, "حذف شد.")
                await callback_query.message.edit_text(f"✅ مورد تقویمی #{hid} حذف شد.")

        await ctx.run_safe_callback(client, callback_query, _run, sensitive=True)

    ctx.register_session_flow(FLOW_HOLIDAY, _handle_holiday_step)


async def _handle_holiday_step(ctx: BotContext, client: Client, message: Message, session: dict) -> None:
    uid = int(message.author.id)
    actor = ctx.services.permissions.require_permission(ctx.get_actor(uid), "MANAGE_HOLIDAYS", "HOLIDAY")
    raw = (message.text or "").strip()
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) != 4:
        raise ValidationError("الگوی صحیح: تاریخ | عنوان | نوع | اطلاع‌رسانی (بله/خیر)")
    notify = parts[3] in {"بله", "بلی", "1", "true", "yes"}
    h_type = parts[2].upper()
    item = ctx.services.notifications.create_holiday(
        actor=actor,
        holiday_date=parts[0],
        title=parts[1],
        holiday_type=h_type,
        is_full_close=(h_type != "EVENT"),
        notify_all=notify,
    )
    ctx.users_repo.clear_session(uid)
    await message.reply(
        f"✅ «{item['title']}» برای تاریخ {format_jalali_date(item['holiday_date'])} ثبت شد.",
        reply_markup=ctx.main_markup_for(actor),
    )
    if notify:
        await ctx.flush_notifications(client)
