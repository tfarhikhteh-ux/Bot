from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from database.db import DatabaseManager
from database.repositories.classes import ClassRepository
from database.repositories.moderation import GroupLockRepository
from database.repositories.settings import SettingsRepository
from utils.validators import ValidationError

from .audit_service import AuditService
from .permission_service import AccessDeniedError, PermissionService

# نقش‌هایی که در تمام گروه‌های مدرسه دسترسی قفل/باز کردن دارند و از قفل‌ها معاف‌اند
GLOBAL_GROUP_LOCK_ROLES = {"ADMIN", "SCHOOL_MANAGER", "DEPUTY", "ASSOCIATION_MEMBER"}

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
    """سرویس مدیریت قفل‌های گروه بله: ادمین/مدیر/ناظم/انجمن در همه گروه‌ها و معلم فقط در گروه کلاس خودش."""

    def __init__(self, db: DatabaseManager, permissions: PermissionService) -> None:
        self.db = db
        self.permissions = permissions
        self.audit = AuditService(db)
        self.repo = GroupLockRepository(db)
        self.classes = ClassRepository(db)
        self.settings = SettingsRepository(db)

    def resolve_group_class_id(self, chat_id: int, chat_title: Optional[str] = None) -> Optional[int]:
        row = self.repo.get_locks(int(chat_id))
        if row.get("class_id"):
            return int(row["class_id"])
        title = (chat_title or row.get("chat_title") or "").strip()
        if title:
            year = self.settings.get("academic_year", "1405-1406")
            cls = self.classes.get_by_name_and_year(title, year)
            if cls and cls.get("is_active"):
                return int(cls["id"])
        return None

    def ensure_can_manage_group(
        self,
        actor: Optional[Dict[str, Any]],
        chat_id: int,
        chat_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        checked = self.permissions.require_permission(actor, "MANAGE_GROUP_LOCKS")
        role = checked["role"].upper()
        if role in GLOBAL_GROUP_LOCK_ROLES:
            return checked

        if role == "TEACHER":
            cls_id = self.resolve_group_class_id(chat_id, chat_title)
            if not cls_id:
                self.audit.log(
                    action="TEACHER_GROUP_LOCK_UNBOUND_DENIED",
                    entity_type="GROUP_LOCK",
                    actor=checked,
                    entity_id=chat_id,
                    result="DENIED",
                    description="تلاش معلم برای تغییر قفل در گروهی که به کلاس او متصل نیست",
                )
                raise AccessDeniedError(
                    "شما فقط در گروه کلاس‌های تخصیص‌یافته به خودتان اجازه مدیریت قفل‌ها را دارید. "
                    "(مدیر یا ناظم می‌تواند با دستور «اتصال کلاس <نام کلاس>» این گروه را به کلاس متصل کند.)"
                )
            self.permissions.ensure_teacher_class_subject(checked, cls_id)
            return checked

        raise AccessDeniedError("شما مجوز مدیریت قفل‌های گروه را ندارید.")

    def bind_group_to_class(
        self,
        actor: Optional[Dict[str, Any]],
        chat_id: int,
        class_id: int,
        chat_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        checked = self.permissions.require_active_user(actor)
        if checked["role"].upper() not in {"ADMIN", "SCHOOL_MANAGER", "DEPUTY"}:
            raise AccessDeniedError("اتصال گروه به کلاس فقط توسط ادمین، مدیر مدرسه یا ناظم مجاز است.")
        cls = self.classes.get_by_id(int(class_id))
        if not cls or not cls.get("is_active"):
            raise ValidationError("کلاس فعال موردنظر یافت نشد.")
        state = self.repo.upsert_locks(
            chat_id=int(chat_id),
            chat_title=chat_title,
            class_id=int(cls["id"]),
            updated_by=checked["id"],
        )
        self.audit.log(
            action="BIND_GROUP_CLASS",
            entity_type="GROUP_LOCK",
            actor=checked,
            entity_id=chat_id,
            after_data={"class_id": cls["id"], "class_name": cls["name"]},
            description=f"اتصال گروه {chat_title or chat_id} به کلاس {cls['name']}",
        )
        return {"locks": state, "class": cls}

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
        checked = self.ensure_can_manage_group(actor, chat_id, chat_title)
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

    def get_group_locks_for_panel(
        self,
        actor: Optional[Dict[str, Any]],
        chat_id: int,
        chat_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.ensure_can_manage_group(actor, chat_id, chat_title)
        return self.repo.get_locks(int(chat_id))

    def _is_sender_exempt(
        self,
        chat_id: int,
        sender_actor: Optional[Dict[str, Any]],
        chat_title: Optional[str] = None,
    ) -> bool:
        if not sender_actor or not sender_actor.get("is_active"):
            return False
        role = sender_actor["role"].upper()
        if role in GLOBAL_GROUP_LOCK_ROLES:
            return True
        if role == "TEACHER":
            cls_id = self.resolve_group_class_id(chat_id, chat_title)
            if not cls_id:
                return False
            try:
                self.permissions.ensure_teacher_class_subject(sender_actor, cls_id)
                return True
            except AccessDeniedError:
                return False
        return False

    def check_violation(
        self,
        chat_id: int,
        sender_actor: Optional[Dict[str, Any]],
        flags: Dict[str, bool],
        chat_title: Optional[str] = None,
    ) -> Optional[str]:
        """بررسی تخلف پیام از قفل‌های فعال گروه؛ ادمین/مدیر/ناظم/انجمن سراسری و معلم در گروه کلاس خودش معاف است."""
        if self._is_sender_exempt(chat_id, sender_actor, chat_title):
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
