from __future__ import annotations

from balethon import Client
from balethon.conditions import equals, private
from balethon.objects import CallbackQuery, Message

from keyboards.admin import BTN_BACKUP_MGMT, backup_menu_inline
from keyboards.main import confirm_inline
from utils.dates import format_jalali_datetime
from utils.helpers import safe_answer_callback
from utils.validators import ValidationError

from . import BotContext, cb_prefix


def register(client: Client, ctx: BotContext) -> None:
    async def _show_backups(client: Client, message: Message):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "BACKUP_CREATE", "BACKUP")
            backups = ctx.services.backups.list_backups(actor)
            lines = ["💾 مدیریت پشتیبان‌گیری و بازیابی پایگاه داده (ویژه ADMIN)", "━" * 26]
            for b in backups[:8]:
                kb_size = round(b["size_bytes"] / 1024, 1)
                lines.append(
                    f"• {b['filename']}\n"
                    f"  حجم: {kb_size} KB | نوع: {b['backup_type']} | تاریخ: {format_jalali_datetime(b['created_at'])}"
                )
            if not backups:
                lines.append("هنوز نسخه پشتیبانی ایجاد نشده است.")
            await message.reply("\n".join(lines), reply_markup=backup_menu_inline(backups))

        await ctx.run_safe_message(client, message, _run)

    @client.on_command(name="پشتیبان")
    async def backup_cmd(*_args, client: Client, message: Message):
        await _show_backups(client, message)

    @client.on_message(private & equals(BTN_BACKUP_MGMT))
    async def backup_btn(*, client: Client, message: Message):
        await _show_backups(client, message)

    @client.on_callback_query(cb_prefix("bkp:"))
    async def backup_callbacks(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "BACKUP_CREATE", "BACKUP")
            parts = callback_query.data.split(":")
            action = parts[1]
            chat_id = callback_query.message.chat.id

            if action == "create":
                rec = ctx.services.backups.create_backup(actor=actor, backup_type="MANUAL")
                await safe_answer_callback(callback_query, "نسخه پشتیبان ایجاد شد.")
                await client.send_message(
                    chat_id,
                    f"✅ پشتیبان جدید با موفقیت تهیه و راستی‌آزمایی شد:\n"
                    f"📁 فایل: {rec['filename']}\n"
                    f"🔐 SHA-256: {rec['sha256'][:24]}...",
                )
            elif action == "ask_restore":
                ctx.services.permissions.require_permission(actor, "BACKUP_RESTORE", "BACKUP")
                bid = int(parts[2])
                row = ctx.services.db.fetchone("SELECT * FROM backups WHERE id = ?;", (bid,))
                if not row:
                    raise ValidationError("نسخه پشتیبان موردنظر یافت نشد.")
                await safe_answer_callback(callback_query, "هشدار بازیابی دیتابیس")
                await client.send_message(
                    chat_id,
                    f"⚠️ هشدار امنیتی بازیابی پایگاه داده:\n"
                    f"آیا از جایگزینی دیتابیس فعلی با پشتیبان «{row['filename']}» اطمینان کامل دارید؟\n"
                    "(یک نسخه پشتیبان محافظتی خودکار پیش از بازیابی تهیه خواهد شد.)",
                    reply_markup=confirm_inline(f"bkp:do_restore:{bid}"),
                )
            elif action == "do_restore":
                ctx.services.permissions.require_permission(actor, "BACKUP_RESTORE", "BACKUP")
                bid = int(parts[2])
                row = ctx.services.db.fetchone("SELECT * FROM backups WHERE id = ?;", (bid,))
                if not row:
                    raise ValidationError("نسخه پشتیبان موردنظر یافت نشد.")
                res = ctx.services.backups.restore_backup(actor, row["filename"], confirmed=True)
                await safe_answer_callback(callback_query, "بازیابی انجام شد.")
                await callback_query.message.edit_text(
                    f"✅ پایگاه داده با موفقیت از «{res['restored_from']}» بازیابی شد.\n"
                    f"🛡 پشتیبان ایمنی پیش از بازیابی: {res['pre_restore_snapshot']}"
                )

        await ctx.run_safe_callback(client, callback_query, _run, sensitive=True)
