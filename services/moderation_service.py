from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from database.db import DatabaseManager
from database.repositories.moderation import GroupLockRepository
from utils.validators import ValidationError

from .audit_service import AuditService
from .permission_service import AccessDeniedError, PermissionService

# تمام کارکنان و اعضای مجاز مدیریت گروه (ادمین، مدیر، ناظم/معاون، معلم، انجمن اولیا)
GROUP_LOCK_EXEMPT_ROLES = {"ADMIN", "SCHOOL_MANAGER", "DEPUTY", "TEACHER", "ASSOCIATION_MEMBER"}

LOCK_ITEMS: List[Tuple[str, str, str]] = [
    ("all", "lock_all", "چت گروه (همه پیام‌ها)"),
    ("gif", "lock_gif", "گیف (تصویر متحرک)"),
    ("sticker", "lock_sticker", "استیکر"),
    ("photo", "lock_photo", "عکس و تصویر"),
    ("video", "lock_video", "فیلم و ویدیو"),
    ("voice", "lock_voice", "پیام صوتی (ویس)"),
    ("audio", "lock_audio", "فایل صوتی و موزیک"),
    ("document", "lock_document", "فایل و سند"),
    ("link", "lock_link", "لینک و نشانی اینترنتی"),
    ("forward", "lock_forward", "پیام فورواردشده"),
    ("contact", "lock_contact", "شماره و مخاطب"),
    ("location", "lock_location", "موقعیت مکانی (لوکیشن)"),
]

LOCK_KEY_TO_COLUMN: Dict[str, str] = {k: col for k, col, _ in LOCK_ITEMS}
LOCK_KEY_TO_LABEL: Dict[str, str] = {k: label for k, _, label in LOCK_ITEMS}

MEDIA_LOCK_KEYS = ("gif", "sticker", "photo", "video", "voice", "audio", "document")

PERSIAN_LOCK_ALIASES: Dict[str, str] = {
    "همه": "all",
    "چت": "all",
    "گروه": "all",
    "پیام": "all",
    "گیف": "gif",
    "gif": "gif",
    "انیمیشن": "gif",
    "استیکر": "sticker",
    "sticker": "sticker",
    "عکس": "photo",
    "تصویر": "photo",
    "photo": "photo",
    "فیلم": "video",
    "ویدیو": "video",
    "ویدئو": "video",
    "video": "video",
    "ویس": "voice",
    "صدا": "voice",
    "voice": "voice",
    "صوت": "audio",
    "آهنگ": "audio",
    "اهنگ": "audio",
    "موزیک": "audio",
    "audio": "audio",
    "فایل": "document",
    "سند": "document",
    "داکیومنت": "document",
    "doc": "document",
    "لینک": "link",
    "پیوند": "link",
    "link": "link",
    "فوروارد": "forward",
    "فروارد": "forward",
    "بازارسال": "forward",
    "forward": "forward",
    "مخاطب": "contact",
    "شماره": "contact",
    "contact": "contact",
    "لوکیشن": "location",
    "مکان": "location",
    "موقعیت": "location",
    "location": "location",
    "رسانه": "media",
    "مدیا": "media",
    "media": "media",
}


class ModerationService:
    """سرویس مدیریت قفل‌های گروه بله (گیف، رسانه، لینک، فوروارد، چت و...) و اعمال خودکار آن‌ها."""

    def __init__(self, db: DatabaseManager, permissions: PermissionService) -> None:
        self.db = db
        self.permissions = permissions
        self.audit = AuditService(db)
        self.repo = GroupLockRepository(db)

    def resolve_target_key(self, raw_target: str) -> str:
        cleaned = (raw_target or "").strip().lower().replace("_", " ").replace("\u200c", " ")
        cleaned_compact = " ".join(cleaned.split())
        if cleaned_compact in PERSIAN_LOCK_ALIASES:
            return PERSIAN_LOCK_ALIASES[cleaned_compact]
        first_word = cleaned_compact.split(" ")[0] if cleaned_compact else ""
        if first_word in PERSIAN_LOCK_ALIASES:
            return PERSIAN_LOCK_ALIASES[first_word]
        raise ValidationError(
            "نوع قفل شناخته نشد. موارد قابل استفاده: گیف، رسانه، استیکر، عکس، فیلم، ویس، صوت، فایل، لینک، فوروارد، مخاطب، لوکیشن، گروه."
        )

    def set_lock(
        self,
        actor: Optional[Dict[str, Any]],
        chat_id: int,
        target: str,
        locked: bool,
        chat_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        checked = self.permissions.require_permission(actor, "MANAGE_GROUP_LOCKS")
        key = target if (target in LOCK_KEY_TO_COLUMN or target == "media") else self.resolve_target_key(target)
        val = 1 if locked else 0

        if key == "media":
            updates = {LOCK_KEY_TO_COLUMN[mk]: val for mk in MEDIA_LOCK_KEYS}
            label = "تمام رسانه‌ها (گیف، استیکر، عکس، فیلم، ویس، صوت، فایل)"
        else:
            updates = {LOCK_KEY_TO_COLUMN[key]: val}
            label = LOCK_KEY_TO_LABEL[key]

        state = self.repo.upsert_locks(
            chat_id=int(chat_id),
            chat_title=chat_title,
            updated_by=checked["id"],
            **updates,
        )
        self.audit.log(
            action="SET_GROUP_LOCK",
            entity_type="GROUP_LOCK",
            actor=checked,
            entity_id=chat_id,
            after_data={"target": key, "locked": bool(locked)},
            description=f"{'قفل کردن' if locked else 'باز کردن'} «{label}» در گروه {chat_title or chat_id}",
        )
        return {"locks": state, "target": key, "label": label, "locked": bool(locked)}

    def toggle_lock(
        self,
        actor: Optional[Dict[str, Any]],
        chat_id: int,
        key: str,
        chat_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        if key not in LOCK_KEY_TO_COLUMN:
            raise ValidationError("کلید قفل نامعتبر است.")
        current = self.repo.get_locks(int(chat_id))
        col = LOCK_KEY_TO_COLUMN[key]
        new_locked = not bool(current.get(col, 0))
        return self.set_lock(actor, chat_id, key, locked=new_locked, chat_title=chat_title)

    def get_group_locks_for_panel(self, actor: Optional[Dict[str, Any]], chat_id: int) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "MANAGE_GROUP_LOCKS")
        return self.repo.get_locks(int(chat_id))

    def check_violation(
        self,
        chat_id: int,
        sender_actor: Optional[Dict[str, Any]],
        flags: Dict[str, bool],
    ) -> Optional[str]:
        """بررسی تخلف پیام از قفل‌های فعال گروه؛ کارکنان و انجمن معاف هستند."""
        if sender_actor and sender_actor.get("is_active") and sender_actor.get("role") in GROUP_LOCK_EXEMPT_ROLES:
            return None

        locks = self.repo.get_locks(int(chat_id))
        if locks.get("lock_all"):
            return "all"

        for key, col, _ in LOCK_ITEMS:
            if key == "all":
                continue
            if locks.get(col) and flags.get(key):
                return key
        return None
