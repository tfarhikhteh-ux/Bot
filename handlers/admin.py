from __future__ import annotations

from balethon import Client
from balethon.conditions import equals, private
from balethon.objects import CallbackQuery, Message

from keyboards.admin import BTN_ADMIN_PANEL, admin_panel_inline
from keyboards.main import wizard_cancel_keyboard
from keyboards.manager import BTN_MANAGER_PANEL
from utils.dates import format_jalali_datetime
from utils.formatters import role_label
from utils.helpers import safe_answer_callback, send_chunked_message
from utils.validators import ValidationError

from . import BotContext, cb_prefix

FLOW_ADMIN = "ADMIN_SETTINGS_FLOW"


def register(client: Client, ctx: BotContext) -> None:
    @client.on_command(name="مدیریت")
    async def management_command(*_args, client: Client, message: Message):
        async def _run(actor):
            # فقط ADMIN و SCHOOL_MANAGER مجاز به اجرای دستور /مدیریت هستند
            checked = ctx.services.permissions.require_roles(actor, "ADMIN", "SCHOOL_MANAGER")
            school_name = ctx.services.notifications.settings.get("school_name", "مدرسه")
            if checked["role"] == "ADMIN":
                await message.reply(
                    f"⚙️ پنل مدیریت کل «{school_name}»\n"
                    "شما دارای بالاترین سطح دسترسی (ADMIN) هستید. یکی از بخش‌ها را انتخاب کنید:",
                    reply_markup=admin_panel_inline(),
                )
            else:
                await message.reply(
                    f"🏛 پنل مدیریت مدرسه «{school_name}»\n"
                    f"مدیر محترم «{checked['full_name']}»، از منوی مدیریتی پایین بخش موردنظر را انتخاب نمایید.",
                    reply_markup=ctx.main_markup_for(checked),
                )

        await ctx.run_safe_message(client, message, _run)

    @client.on_message(private & equals(BTN_ADMIN_PANEL))
    async def admin_panel_button(*, client: Client, message: Message):
        async def _run(actor):
            ctx.services.permissions.require_roles(actor, "ADMIN")
            await message.reply(
                "⚙️ پنل اختصاصی ادمین کل سامانه:\nبخش موردنظر را انتخاب نمایید:",
                reply_markup=admin_panel_inline(),
            )

        await ctx.run_safe_message(client, message, _run)

    @client.on_message(private & equals(BTN_MANAGER_PANEL))
    async def manager_panel_button(*, client: Client, message: Message):
        async def _run(actor):
            checked = ctx.services.permissions.require_roles(actor, "ADMIN", "SCHOOL_MANAGER")
            overview = ctx.services.reports.get_dashboard_overview(checked)
            await message.reply(
                "🏛 خلاصه وضعیت اجرایی مدرسه:\n"
                f"▫️ کل کاربران فعال: {overview['total_users']}\n"
                f"▫️ دانش‌آموزان: {overview['students_count']} | دبیران: {overview['teachers_count']}\n"
                f"▫️ کلاس‌های فعال: {overview['classes_count']}\n"
                f"▫️ درخواست‌های ثبت‌نام در انتظار: {overview['pending_registrations']}",
                reply_markup=ctx.main_markup_for(checked),
            )

        await ctx.run_safe_message(client, message, _run)

    @client.on_callback_query(cb_prefix("adm:"))
    async def admin_callbacks(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            ctx.services.permissions.require_roles(actor, "ADMIN")
            action = callback_query.data.split(":", 1)[1]
            uid = int(callback_query.author.id)

            if action in {"add_manager", "add_admin"}:
                target_role = "SCHOOL_MANAGER" if action == "add_manager" else "ADMIN"
                ctx.users_repo.set_session(uid, FLOW_ADMIN, "ADD_PRIVILEGED_BALE_ID", {"role": target_role})
                await safe_answer_callback(callback_query, "شناسه بله را وارد کنید.")
                await client.send_message(
                    callback_query.message.chat.id,
                    f"➕ افزودن «{role_label(target_role)}»\nلطفاً شناسه عددی بله کاربر را وارد کنید:",
                    reply_markup=wizard_cancel_keyboard(),
                )
            elif action == "settings":
                rows = ctx.services.notifications.settings.list_all(include_admin_only=True)
                lines = ["🔧 تنظیمات فعلی سامانه:", "━" * 22]
                for r in rows:
                    lock = "🔒" if r["admin_only"] else "🔹"
                    lines.append(f"{lock} {r['key']} = {r['value']}")
                lines.append("\nبرای تغییر هر تنظیم، عبارت کلید=مقدار را در مرحله بعد ارسال کنید.")
                ctx.users_repo.set_session(uid, FLOW_ADMIN, "EDIT_SETTING", {})
                await safe_answer_callback(callback_query, "تنظیمات بارگذاری شد.")
                await send_chunked_message(
                    client, callback_query.message.chat.id, "\n".join(lines), reply_markup=wizard_cancel_keyboard()
                )
            elif action == "set_mgmt_group":
                ctx.users_repo.set_session(uid, FLOW_ADMIN, "SET_MGMT_GROUP", {})
                await safe_answer_callback(callback_query, "شناسه گروه مدیریتی را وارد کنید.")
                await client.send_message(
                    callback_query.message.chat.id,
                    "👥 شناسه عددی گروه مدیریتی در بله را ارسال کنید (می‌توانید در گروه دستور /start بزنید تا شناسه نمایش داده شود):",
                    reply_markup=wizard_cancel_keyboard(),
                )
            elif action == "audit_recent":
                ctx.services.permissions.require_permission(actor, "VIEW_SECURITY_AUDIT")
                logs = ctx.services.audit.search_logs(limit=15)
                lines = ["🕵️ ۱۵ رویداد اخیر ممیزی امنیتی:", "━" * 24]
                for lg in logs:
                    who = lg.get("actor_name") or lg.get("actor_bale_id") or "سیستم"
                    lines.append(
                        f"• [{lg['result']}] {lg['action']} | {who} | {format_jalali_datetime(lg['created_at'])}\n"
                        f"  {lg.get('description') or ''}"
                    )
                await safe_answer_callback(callback_query, "گزارش آماده شد.")
                await send_chunked_message(client, callback_query.message.chat.id, "\n".join(lines))

        await ctx.run_safe_callback(client, callback_query, _run)

    ctx.register_session_flow(FLOW_ADMIN, _handle_admin_step)


async def _handle_admin_step(ctx: BotContext, client: Client, message: Message, session: dict) -> None:
    uid = int(message.author.id)
    actor = ctx.services.permissions.require_roles(ctx.get_actor(uid), "ADMIN")
    step = session["step"]
    data = session["data"]
    raw = (message.text or "").strip()

    if step == "ADD_PRIVILEGED_BALE_ID":
        if not raw.isdigit():
            raise ValidationError("شناسه بله باید عددی مثبت باشد.")
        bale_id = int(raw)
        existing = ctx.users_repo.get_by_bale_id(bale_id)
        if existing:
            updated = ctx.services.registration.change_user_role(actor, existing["id"], data["role"])
            ctx.users_repo.clear_session(uid)
            await message.reply(
                f"✅ نقش کاربر «{updated['full_name']}» به «{role_label(updated['role'])}» ارتقا یافت.",
                reply_markup=ctx.main_markup_for(actor),
            )
            return
        data["target_bale_id"] = bale_id
        ctx.users_repo.set_session(uid, FLOW_ADMIN, "ADD_PRIVILEGED_NAME", data)
        await message.reply("👤 نام و نام خانوادگی ایشان را وارد کنید:")
        return

    if step == "ADD_PRIVILEGED_NAME":
        created = ctx.services.registration.create_user_direct(
            actor=actor,
            bale_user_id=int(data["target_bale_id"]),
            full_name=raw,
            role=data["role"],
        )
        ctx.users_repo.clear_session(uid)
        await message.reply(
            f"✅ کاربر «{created['full_name']}» با نقش «{role_label(created['role'])}» ایجاد شد.",
            reply_markup=ctx.main_markup_for(actor),
        )
        return

    if step == "EDIT_SETTING":
        if "=" not in raw:
            raise ValidationError("لطفاً به صورت کلید=مقدار وارد کنید (مثال: school_name=دبیرستان البرز).")
        k, v = [p.strip() for p in raw.split("=", 1)]
        ctx.services.notifications.update_setting(actor, k, v)
        ctx.users_repo.clear_session(uid)
        await message.reply(f"✅ تنظیم «{k}» با موفقیت ذخیره شد.", reply_markup=ctx.main_markup_for(actor))
        return

    if step == "SET_MGMT_GROUP":
        if not raw.lstrip("-").isdigit() or int(raw) == 0:
            raise ValidationError("شناسه گروه باید عددی معتبر باشد.")
        ctx.services.notifications.update_setting(actor, "management_group_id", raw)
        ctx.users_repo.clear_session(uid)
        await message.reply(f"✅ شناسه گروه مدیریتی بر روی {raw} تنظیم شد.", reply_markup=ctx.main_markup_for(actor))
        return
