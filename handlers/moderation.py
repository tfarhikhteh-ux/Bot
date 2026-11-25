from __future__ import annotations

import logging
import re
from typing import Dict, Optional, Tuple

from balethon import Client
from balethon.conditions import Condition
from balethon.objects import CallbackQuery, InlineKeyboard, InlineKeyboardButton, Message

from services.moderation_service import LOCK_ITEMS, LOCK_KEY_TO_LABEL
from utils.helpers import safe_answer_callback
from utils.logging_config import redact_sensitive_text
from utils.validators import ValidationError

from . import BotContext, cb_prefix

log = logging.getLogger("school_bot.moderation")

_LINK_RE = re.compile(r"(?:https?://|www\.|ble\.ir/|t\.me/)\S+", re.IGNORECASE)

# الگوهای باز کردن قفل باید قبل از الگوی قفل کردن بررسی شوند تا «قفل باز گیف» به درستی «باز کردن» تشخیص داده شود
_UNLOCK_CMD_RE = re.compile(
    r"^(?:/?(?:قفل[_\s]+باز|باز(?:[_\s]*کردن)?|آزاد(?:[_\s]*کردن)?)|/unlock)[_\s]+(.+)$",
    re.IGNORECASE,
)
_LOCK_CMD_RE = re.compile(
    r"^(?:/?(?:قفل|بستن)|/lock)[_\s]+(.+)$",
    re.IGNORECASE,
)
_PANEL_WORDS = {"پنل قفل", "وضعیت قفل", "وضعیت قفل ها", "وضعیت قفل‌ها", "قفل ها", "قفل‌ها", "/قفل", "/locks"}


def _is_group_chat(msg: Optional[Message]) -> bool:
    if not msg or not msg.chat or msg.chat.type is None:
        return False
    val = str(getattr(msg.chat.type, "value", msg.chat.type)).lower()
    return val.endswith("group")


def extract_message_media_flags(msg: Message) -> Dict[str, bool]:
    """استخراج دقیق نوع محتوا منطبق با مستندات رسمی API بازوی بله."""
    doc = getattr(msg, "document", None)
    doc_mime = (getattr(doc, "mime_type", None) or "").lower() if doc else ""
    doc_name = (getattr(doc, "file_name", None) or "").lower() if doc else ""

    # در API بله برای سازگاری عقب‌رو هنگام ارسال animation، فیلد document نیز پر می‌شود
    is_gif = bool(getattr(msg, "animation", None)) or (
        doc is not None and (doc_mime == "image/gif" or doc_name.endswith(".gif"))
    )
    is_voice = bool(getattr(msg, "voice", None))
    is_audio = bool(getattr(msg, "audio", None)) and not is_voice
    is_sticker = bool(getattr(msg, "sticker", None))
    is_photo = bool(getattr(msg, "photo", None))
    is_video = bool(getattr(msg, "video", None))
    is_document = bool(doc is not None and not is_gif and not is_audio)
    is_contact = bool(getattr(msg, "contact", None))
    is_location = bool(getattr(msg, "location", None))
    is_forward = bool(
        getattr(msg, "forward_from", None)
        or getattr(msg, "forward_from_chat", None)
        or getattr(msg, "forward_date", None)
    )

    txt = f"{getattr(msg, 'text', None) or ''} {getattr(msg, 'caption', None) or ''}"
    is_link = bool(_LINK_RE.search(txt))

    return {
        "gif": is_gif,
        "sticker": is_sticker,
        "photo": is_photo,
        "video": is_video,
        "voice": is_voice,
        "audio": is_audio,
        "document": is_document,
        "link": is_link,
        "forward": is_forward,
        "contact": is_contact,
        "location": is_location,
    }


def parse_lock_command(text: Optional[str]) -> Optional[Tuple[str, Optional[str]]]:
    """برگشت (action, target): action یکی از 'lock'، 'unlock' یا 'panel' است."""
    if not text:
        return None
    cleaned = " ".join(text.strip().split())
    if cleaned.lower() in _PANEL_WORDS:
        return ("panel", None)

    m_un = _UNLOCK_CMD_RE.match(cleaned)
    if m_un:
        return ("unlock", m_un.group(1).strip())

    m_lk = _LOCK_CMD_RE.match(cleaned)
    if m_lk:
        target = m_lk.group(1).strip()
        if target in {"ها", "ها گروه"}:
            return ("panel", None)
        return ("lock", target)

    return None


def _build_locks_inline_panel(chat_id: int, locks: dict) -> InlineKeyboard:
    short_titles = {
        "all": "چت گروه",
        "gif": "گیف",
        "sticker": "استیکر",
        "photo": "عکس",
        "video": "فیلم",
        "voice": "ویس",
        "audio": "صوت",
        "document": "فایل",
        "link": "لینک",
        "forward": "فوروارد",
        "contact": "مخاطب",
        "location": "لوکیشن",
    }
    kb = InlineKeyboard()
    row = []
    for key, col, _ in LOCK_ITEMS:
        is_locked = bool(locks.get(col, 0))
        icon = "🔒" if is_locked else "🔓"
        state_word = "قفل" if is_locked else "باز"
        btn_style = "danger" if is_locked else "success"
        row.append(
            InlineKeyboardButton(
                f"{icon} {short_titles[key]}: {state_word}",
                callback_data=f"glk:tog:{chat_id}:{key}",
                style=btn_style,
            )
        )
        if len(row) == 2:
            kb.add_row(*row)
            row = []
    if row:
        kb.add_row(*row)

    kb.add_row(
        InlineKeyboardButton("🔒 قفل همه رسانه‌ها", callback_data=f"glk:med:{chat_id}:1", style="danger"),
        InlineKeyboardButton("🔓 باز کردن همه رسانه‌ها", callback_data=f"glk:med:{chat_id}:0", style="success"),
    )
    return kb


def _format_locks_panel_text(chat_title: Optional[str], locks: dict) -> str:
    lines = [f"🛡 پنل مدیریت قفل‌ها و رسانه‌های گروه «{chat_title or 'گروه'}»:", ""]
    for key, col, label in LOCK_ITEMS:
        status = "🔒 قفل (مسدود برای دانش‌آموزان)" if locks.get(col) else "🔓 باز (آزاد)"
        lines.append(f"• {label}: {status}")
    lines.append("\n💡 با کلیک روی هر دکمه وضعیت آن تغییر می‌کند (ادمین، مدیر، ناظم، دبیر و انجمن از قفل‌ها معاف‌اند).")
    return "\n".join(lines)


def register_enforcer(client: Client, ctx: BotContext) -> None:
    """ثبت نگهبان لحظه‌ای گروه در بالاترین اولویت زنجیره رویدادهای بله (پیام جدید و پیام ویرایش‌شده)."""

    class _ViolationCondition(Condition):
        def __init__(self) -> None:
            super().__init__(can_process=Message)

        async def __call__(self, _client, event: Message) -> bool:
            if not _is_group_chat(event) or not event.author:
                return False
            actor = ctx.get_actor(int(event.author.id))
            flags = extract_message_media_flags(event)
            violation = ctx.services.moderation.check_violation(int(event.chat.id), actor, flags)
            return violation is not None

    cond = _ViolationCondition()

    async def _delete_violating_message(client: Client, message: Message) -> None:
        if not message.chat or message.id is None:
            return
        try:
            await client.delete_message(int(message.chat.id), int(message.id))
        except Exception as exc:
            log.warning(
                "حذف پیام غیرمجاز در گروه %s انجام نشد (بررسی کنید بازو در گروه ادمین باشد): %s",
                message.chat.id,
                redact_sensitive_text(str(exc)),
            )

    @client.on_message(cond)
    async def enforce_on_new_message(*, client: Client, message: Message):
        await _delete_violating_message(client, message)

    @client.on_edited_message(cond)
    async def enforce_on_edited_message(*, client: Client, edited_message: Message):
        await _delete_violating_message(client, edited_message)


def register(client: Client, ctx: BotContext) -> None:
    class _GroupModCmdCondition(Condition):
        def __init__(self) -> None:
            super().__init__(can_process=Message)

        async def __call__(self, _client, event: Message) -> bool:
            if not _is_group_chat(event) or not event.text:
                return False
            txt = event.text.strip()
            if parse_lock_command(txt) is not None:
                return True
            return txt in {"پین", "سنجاق", "حذف پین", "حذف سنجاق", "حذف پیام", "مسدود کاربر", "رفع مسدودیت کاربر"}

    @client.on_command(name="locks")
    async def locks_slash_cmd(*_args, client: Client, message: Message):
        if not _is_group_chat(message):
            await message.reply("🛡 این دستور مخصوص گروه‌های مدرسه است. آن را در داخل گروه موردنظر ارسال کنید.")
            return
        await _handle_group_panel(client, message, ctx)

    @client.on_command(name="قفل")
    async def locks_persian_slash_cmd(*_args, client: Client, message: Message):
        if not _is_group_chat(message):
            await message.reply("🛡 برای مدیریت قفل گیف و رسانه، دستور را در داخل گروه مدرسه ارسال کنید (مثلاً: قفل گیف / قفل باز گیف / پنل قفل).")
            return
        parsed = parse_lock_command(message.text)
        if parsed and parsed[0] in {"lock", "unlock"} and parsed[1]:
            await _handle_lock_toggle_text(client, message, ctx, locked=(parsed[0] == "lock"), target=parsed[1])
        else:
            await _handle_group_panel(client, message, ctx)

    @client.on_message(_GroupModCmdCondition())
    async def group_moderation_text_handler(*, client: Client, message: Message):
        txt = (message.text or "").strip()
        parsed = parse_lock_command(txt)
        if parsed:
            action, target = parsed
            if action == "panel":
                await _handle_group_panel(client, message, ctx)
            else:
                await _handle_lock_toggle_text(client, message, ctx, locked=(action == "lock"), target=target or "")
            return

        async def _run_admin_action(actor):
            checked = ctx.services.permissions.require_permission(actor, "MANAGE_GROUP_LOCKS")
            if not message.reply_to_message or message.reply_to_message.id is None:
                raise ValidationError(f"برای اجرای دستور «{txt}» باید روی پیام موردنظر در گروه ریپلای کنید.")

            chat_id = int(message.chat.id)
            target_msg_id = int(message.reply_to_message.id)

            if txt in {"پین", "سنجاق"}:
                await client.pin_chat_message(chat_id, target_msg_id)
                await message.reply("📌 پیام موردنظر در گروه سنجاق (Pin) شد.")
            elif txt in {"حذف پین", "حذف سنجاق"}:
                await client.unpin_chat_message(chat_id, target_msg_id)
                await message.reply("📌 پیام از فهرست سنجاق‌شده‌ها خارج شد.")
            elif txt == "حذف پیام":
                await client.delete_message(chat_id, target_msg_id)
                try:
                    if message.id is not None:
                        await client.delete_message(chat_id, int(message.id))
                except Exception:
                    pass
            elif txt in {"مسدود کاربر", "رفع مسدودیت کاربر"}:
                if checked["role"] not in {"ADMIN", "SCHOOL_MANAGER", "DEPUTY"}:
                    raise ValidationError("مسدودسازی و رفع مسدودیت عضو در گروه فقط ویژه ادمین، مدیر و ناظم است.")
                if not message.reply_to_message.author:
                    raise ValidationError("شناسه فرستنده پیام مشخص نیست.")
                target_uid = int(message.reply_to_message.author.id)
                target_user = ctx.get_actor(target_uid)
                if target_user and target_user["role"] in {"ADMIN", "SCHOOL_MANAGER", "DEPUTY", "TEACHER"}:
                    raise ValidationError("امکان مسدود کردن کادر مدرسه در گروه وجود ندارد.")
                if txt == "مسدود کاربر":
                    await client.ban_chat_member(chat_id, target_uid)
                    await message.reply("🚫 کاربر موردنظر از گروه مسدود شد.")
                else:
                    await client.unban_chat_member(chat_id, target_uid, only_if_banned=True)
                    await message.reply("✅ مسدودیت کاربر در گروه برطرف شد.")

        await ctx.run_safe_message(client, message, _run_admin_action)

    @client.on_callback_query(cb_prefix("glk:"))
    async def group_locks_callback(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            parts = (callback_query.data or "").split(":")
            sub = parts[1]
            chat_id = int(parts[2])
            chat_title = getattr(callback_query.message.chat, "title", None) if callback_query.message else None

            if sub == "tog":
                key = parts[3]
                res = ctx.services.moderation.toggle_lock(actor, chat_id, key, chat_title=chat_title)
            elif sub == "med":
                locked = parts[3] == "1"
                res = ctx.services.moderation.set_lock(actor, chat_id, "media", locked=locked, chat_title=chat_title)
            else:
                return

            state_str = "قفل شد 🔒" if res["locked"] else "باز شد 🔓"
            await safe_answer_callback(callback_query, f"{res['label']}: {state_str}")
            if callback_query.message:
                await callback_query.message.edit_text(
                    _format_locks_panel_text(chat_title, res["locks"]),
                    reply_markup=_build_locks_inline_panel(chat_id, res["locks"]),
                )

        await ctx.run_safe_callback(client, callback_query, _run)


async def _handle_group_panel(client: Client, message: Message, ctx: BotContext) -> None:
    async def _run(actor):
        chat_id = int(message.chat.id)
        chat_title = getattr(message.chat, "title", None)
        locks = ctx.services.moderation.get_group_locks_for_panel(actor, chat_id)
        await message.reply(
            _format_locks_panel_text(chat_title, locks),
            reply_markup=_build_locks_inline_panel(chat_id, locks),
        )

    await ctx.run_safe_message(client, message, _run)


async def _handle_lock_toggle_text(
    client: Client,
    message: Message,
    ctx: BotContext,
    *,
    locked: bool,
    target: str,
) -> None:
    async def _run(actor):
        chat_id = int(message.chat.id)
        chat_title = getattr(message.chat, "title", None)
        res = ctx.services.moderation.set_lock(actor, chat_id, target, locked=locked, chat_title=chat_title)
        icon = "🔒" if res["locked"] else "🔓"
        verb = "قفل شد (ارسال برای دانش‌آموزان بسته شد)" if res["locked"] else "باز شد (ارسال آزاد شد)"
        await message.reply(f"{icon} «{res['label']}» در این گروه {verb}.")

    await ctx.run_safe_message(client, message, _run)
