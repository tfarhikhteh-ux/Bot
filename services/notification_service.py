from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional

from database.db import DatabaseManager
from database.repositories.announcements import AnnouncementRepository
from database.repositories.attendance import AttendanceRepository
from database.repositories.classes import ClassRepository
from database.repositories.holidays import HolidayRepository
from database.repositories.settings import SettingsRepository
from database.repositories.subjects import SubjectRepository
from database.repositories.users import UserRepository
from utils.dates import format_jalali_date, now_iso, now_tehran, tehran_weekday_index, today_date, today_iso
from utils.formatters import holiday_type_label
from utils.logging_config import redact_sensitive_text
from utils.validators import (
    VALID_HOLIDAY_TYPES,
    ValidationError,
    sanitize_text,
    validate_date,
    validate_datetime,
    validate_role,
)

from .audit_service import AuditService
from .backup_service import BackupService
from .permission_service import AccessDeniedError, PermissionService

log = logging.getLogger("school_bot.notifications")


class NotificationService:
    """مدیریت اطلاعیه‌ها، صف ارسال پیام با تلاش مجدد، تقویم تعطیلات، تنظیمات و زمان‌بندی خودکار."""

    def __init__(
        self,
        db: DatabaseManager,
        permissions: PermissionService,
        management_group_id: Optional[int] = None,
    ) -> None:
        self.db = db
        self.permissions = permissions
        self.announcements = AnnouncementRepository(db)
        self.holidays = HolidayRepository(db)
        self.settings = SettingsRepository(db)
        self.users = UserRepository(db)
        self.classes = ClassRepository(db)
        self.subjects = SubjectRepository(db)
        self.attendance = AttendanceRepository(db)
        self.audit = AuditService(db)
        self._fallback_mgmt_group_id = management_group_id

    def get_management_group_id(self) -> Optional[int]:
        raw = self.settings.get("management_group_id", "").strip()
        if raw and raw.lstrip("-").isdigit():
            v = int(raw)
            if v != 0:
                return v
        return self._fallback_mgmt_group_id

    def _resolve_recipients(self, target_type: str, target_value: Optional[str]) -> List[Dict[str, Any]]:
        tt = target_type.upper()
        seen_chats = set()
        recipients: List[Dict[str, Any]] = []

        def add_user_rows(rows: List[Dict[str, Any]]) -> None:
            for u in rows:
                cid = u.get("bale_user_id")
                if cid and cid not in seen_chats:
                    seen_chats.add(cid)
                    recipients.append({"chat_id": int(cid), "user_id": u["id"]})

        if tt == "ALL":
            add_user_rows(self.users.search(active_only=True, limit=500))
        elif tt == "ROLE":
            r = validate_role(target_value or "")
            add_user_rows(self.users.search(role=r, active_only=True, limit=500))
        elif tt == "TEACHERS":
            add_user_rows(self.users.search(role="TEACHER", active_only=True, limit=500))
        elif tt == "PARENTS":
            add_user_rows(self.users.search(role="PARENT", active_only=True, limit=500))
        elif tt == "CLASS":
            if not target_value or not str(target_value).isdigit():
                raise ValidationError("شناسه کلاس برای ارسال اطلاعیه کلاسی الزامی است.")
            cid = int(target_value)
            students = self.classes.list_class_students(cid, active_users_only=True)
            add_user_rows(students)
            for s in students:
                add_user_rows(self.users.get_parents_of_student(s["id"]))
        elif tt == "MANAGEMENT_GROUP":
            gid = self.get_management_group_id()
            if gid:
                recipients.append({"chat_id": int(gid), "user_id": None})
        else:
            raise ValidationError("مقصد ارسال اطلاعیه نامعتبر است.")
        return recipients

    def create_announcement(
        self,
        actor: Dict[str, Any],
        title: str,
        body: str,
        target_type: str = "ALL",
        target_value: Optional[str] = None,
        scheduled_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "MANAGE_ANNOUNCEMENTS", "ANNOUNCEMENT")
        clean_title = sanitize_text(title, 100, field_label="عنوان اطلاعیه")
        clean_body = sanitize_text(body, 2500, field_label="متن اطلاعیه")
        tt = (target_type or "ALL").strip().upper()
        if tt in {"ALL", "ROLE", "TEACHERS", "PARENTS"} and actor["role"] == "DEPUTY" and tt == "MANAGEMENT_GROUP":
            raise AccessDeniedError("ارسال مستقیم به گروه مدیریتی نیاز به مجوز مدیر یا ادمین دارد.")

        iso_sched = validate_datetime(scheduled_at) if scheduled_at else None
        ann = self.announcements.create(
            title=clean_title,
            body=clean_body,
            target_type=tt,
            created_by=actor["id"],
            target_value=target_value,
            scheduled_at=iso_sched,
            status="QUEUED",
        )

        # اگر زمان‌بندی فوری است یا زمان آن فرا رسیده است، پیام‌ها فوراً در صف ارسال قرار می‌گیرند
        if iso_sched is None or iso_sched <= now_iso():
            self.enqueue_announcement(ann["id"])

        self.audit.log(
            action="CREATE_ANNOUNCEMENT",
            entity_type="ANNOUNCEMENT",
            actor=actor,
            entity_id=ann["id"],
            after_data=ann,
            description=f"ایجاد اطلاعیه «{clean_title}» با مقصد {tt}",
        )
        return self.announcements.get_by_id(ann["id"])

    def enqueue_announcement(self, announcement_id: int) -> int:
        ann = self.announcements.get_by_id(announcement_id)
        if not ann:
            return 0
        recipients = self._resolve_recipients(ann["target_type"], ann.get("target_value"))
        formatted = f"📢 اطلاعیه مدرسه: {ann['title']}\n{'━' * 20}\n{ann['body']}"
        count = self.announcements.enqueue_messages(
            recipients=recipients,
            text=formatted,
            message_type="ANNOUNCEMENT",
            announcement_id=ann["id"],
        )
        if count == 0:
            self.db.execute(
                "UPDATE announcements SET status = 'SENT', sent_at = ?, updated_at = ? WHERE id = ?;",
                (now_iso(), now_iso(), ann["id"]),
            )
        else:
            self.db.execute(
                "UPDATE announcements SET status = 'SENDING', updated_at = ? WHERE id = ?;",
                (now_iso(), ann["id"]),
            )
        return count

    def edit_announcement(
        self,
        actor: Dict[str, Any],
        announcement_id: int,
        title: str,
        body: str,
        scheduled_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "MANAGE_ANNOUNCEMENTS", "ANNOUNCEMENT", announcement_id)
        before = self.announcements.get_by_id(announcement_id)
        if not before:
            raise ValidationError("اطلاعیه موردنظر یافت نشد.")
        iso_sched = validate_datetime(scheduled_at) if scheduled_at else None
        updated = self.announcements.update(
            announcement_id,
            sanitize_text(title, 100, field_label="عنوان"),
            sanitize_text(body, 2500, field_label="متن"),
            iso_sched,
        )
        self.audit.log(
            action="UPDATE_ANNOUNCEMENT",
            entity_type="ANNOUNCEMENT",
            actor=actor,
            entity_id=announcement_id,
            before_data=before,
            after_data=updated,
            description=f"ویرایش اطلاعیه #{announcement_id}",
        )
        return updated

    def delete_announcement(self, actor: Dict[str, Any], announcement_id: int) -> None:
        self.permissions.require_permission(actor, "MANAGE_ANNOUNCEMENTS", "ANNOUNCEMENT", announcement_id)
        before = self.announcements.get_by_id(announcement_id)
        if not before:
            raise ValidationError("اطلاعیه یافت نشد.")
        self.announcements.soft_delete(announcement_id)
        self.audit.log(
            action="DELETE_ANNOUNCEMENT",
            entity_type="ANNOUNCEMENT",
            actor=actor,
            entity_id=announcement_id,
            before_data=before,
            description=f"حذف کنترل‌شده اطلاعیه #{announcement_id}",
        )

    async def dispatch_queue_batch(
        self,
        send_fn: Callable[[int, str], Awaitable[Any]],
        batch_size: int = 15,
        rate_delay_seconds: float = 0.15,
    ) -> Dict[str, int]:
        """ارسال ایمن دسته‌ای صف پیام با جداسازی خطای تک‌کاربر، رعایت نرخ ارسال و ثبت نتیجه."""
        # ابتدا اطلاعیه‌های زمان‌بندی‌شده سررسیدشده را وارد صف کن
        for due_ann in self.announcements.due_scheduled_announcements():
            self.enqueue_announcement(due_ann["id"])

        items = self.announcements.fetch_pending_queue_batch(batch_size=batch_size)
        sent = 0
        failed = 0
        touched_announcements = set()

        for item in items:
            try:
                await send_fn(int(item["recipient_chat_id"]), item["text"])
                self.announcements.mark_queue_sent(item["id"])
                sent += 1
            except Exception as exc:
                err_txt = redact_sensitive_text(str(exc))
                log.warning("خطا در ارسال پیام صف #%s به %s: %s", item["id"], item["recipient_chat_id"], err_txt)
                self.announcements.mark_queue_failure(item["id"], err_txt)
                failed += 1

            if item.get("announcement_id"):
                touched_announcements.add(int(item["announcement_id"]))

            if rate_delay_seconds > 0:
                await asyncio.sleep(rate_delay_seconds)

        for aid in touched_announcements:
            self.announcements.refresh_announcement_status(aid)

        return {"processed": len(items), "sent": sent, "failed": failed}

    # ---------- مدیریت تعطیلات و تقویم مدرسه ----------
    def create_holiday(
        self,
        actor: Dict[str, Any],
        holiday_date: str,
        title: str,
        holiday_type: str = "OFFICIAL",
        end_date: Optional[str] = None,
        is_full_close: bool = True,
        description: Optional[str] = None,
        notify_all: bool = False,
    ) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "MANAGE_HOLIDAYS", "HOLIDAY")
        s_date = validate_date(holiday_date)
        e_date = validate_date(end_date) if end_date else s_date
        h_type = (holiday_type or "OFFICIAL").strip().upper()
        if h_type not in VALID_HOLIDAY_TYPES:
            raise ValidationError("نوع تعطیلی یا رویداد معتبر نیست.")
        clean_title = sanitize_text(title, 100, field_label="عنوان تعطیلی/رویداد")
        clean_desc = sanitize_text(description, 400, allow_empty=True) or None

        try:
            item = self.holidays.create(
                holiday_date=s_date,
                title=clean_title,
                holiday_type=h_type,
                end_date=e_date,
                is_full_close=1 if (is_full_close and h_type != "EVENT") else 0,
                description=clean_desc,
                created_by=actor["id"],
            )
        except Exception as exc:
            raise ValidationError(f"خطا در ثبت تقویم: {exc}") from exc

        self.audit.log(
            action="CREATE_HOLIDAY",
            entity_type="HOLIDAY",
            actor=actor,
            entity_id=item["id"],
            after_data=item,
            description=f"ثبت {holiday_type_label(h_type)}: {clean_title} ({s_date})",
        )

        if notify_all:
            recipients = self._resolve_recipients("ALL", None)
            date_txt = format_jalali_date(s_date) if s_date == e_date else f"{format_jalali_date(s_date)} تا {format_jalali_date(e_date)}"
            msg = (
                f"🗓 اطلاعیه تقویم مدرسه ({holiday_type_label(h_type)})\n"
                f"📌 عنوان: {clean_title}\n"
                f"📅 تاریخ: {date_txt}"
            )
            if clean_desc:
                msg += f"\n📝 توضیحات: {clean_desc}"
            self.announcements.enqueue_messages(recipients, msg, message_type="HOLIDAY_ALERT")
            self.holidays.mark_notified(item["id"])
        return item

    def delete_holiday(self, actor: Dict[str, Any], holiday_id: int) -> None:
        self.permissions.require_permission(actor, "MANAGE_HOLIDAYS", "HOLIDAY", holiday_id)
        before = self.holidays.get_by_id(holiday_id)
        if not before:
            raise ValidationError("مورد تقویمی یافت نشد.")
        self.holidays.delete(holiday_id)
        self.audit.log(
            action="DELETE_HOLIDAY",
            entity_type="HOLIDAY",
            actor=actor,
            entity_id=holiday_id,
            before_data=before,
            description=f"حذف مورد تقویمی {before['title']}",
        )

    # ---------- مدیریت تنظیمات مدرسه و تنظیمات امنیتی ادمین ----------
    def update_setting(self, actor: Dict[str, Any], key: str, value: str) -> None:
        existing = self.settings.get_row(key)
        if existing and existing["admin_only"]:
            self.permissions.require_permission(actor, "MANAGE_SECURITY_SETTINGS", "SETTING", key)
        else:
            self.permissions.require_permission(actor, "MANAGE_SCHOOL_SETTINGS", "SETTING", key)

        if key in {"term1_start", "term1_end", "term2_start", "term2_end"}:
            value = validate_date(value)
        elif key == "teaching_days":
            days = [int(x.strip()) for x in value.strip("[] ").split(",") if x.strip() != ""]
            if not days or any(d < 0 or d > 6 for d in days):
                raise ValidationError("روزهای آموزشی باید فهرستی از اعداد 0 تا 6 باشند.")
            value = json.dumps(sorted(set(days)))

        old_val = existing["value"] if existing else None
        self.settings.set_value(
            key=key,
            value=value,
            updated_by=actor["id"],
            category=existing["category"] if existing else "GENERAL",
            admin_only=existing["admin_only"] if existing else 0,
        )
        self.audit.log(
            action="UPDATE_SETTING",
            entity_type="SETTING",
            actor=actor,
            entity_id=key,
            before_data={"value": old_val},
            after_data={"value": value},
            description=f"تغییر تنظیم {key}",
        )

    # ---------- چرخه وظایف زمان‌بندی‌شده خودکار و مقاوم در برابر ری‌استارت ----------
    def run_scheduled_tasks_tick(
        self,
        backup_service: Optional[BackupService] = None,
        auto_backup_interval_hours: int = 24,
    ) -> Dict[str, Any]:
        executed: List[str] = []
        now_dt = now_tehran()
        today_str = today_iso()

        # ۱. اطلاعیه‌های زمان‌بندی‌شده سررسیدشده
        due_anns = self.announcements.due_scheduled_announcements()
        for ann in due_anns:
            self.enqueue_announcement(ann["id"])
            executed.append(f"announcement:{ann['id']}")

        # ۲. پشتیبان‌گیری خودکار زمان‌بندی‌شده
        if backup_service and self.settings.get_bool("auto_backup_enabled", True):
            bucket = int(now_dt.timestamp() // (max(auto_backup_interval_hours, 1) * 3600))
            slot = f"backup_bucket_{bucket}"
            if self.settings.claim_task_slot("scheduled_backup", "BACKUP", slot):
                b_rec = backup_service.create_backup(actor=None, backup_type="SCHEDULED")
                executed.append(f"scheduled_backup:{b_rec['filename']}")

        # ۳. یادآوری تعطیلات و رویدادهای فردای مدرسه
        tomorrow_iso = (today_date() + timedelta(days=1)).isoformat()
        upcoming = self.holidays.list_holidays(date_from=tomorrow_iso, date_to=tomorrow_iso)
        for hol in upcoming:
            slot_key = f"holiday_reminder_{hol['id']}"
            if self.settings.claim_task_slot(slot_key, "HOLIDAY_REMINDER", tomorrow_iso):
                recipients = self._resolve_recipients("ALL", None)
                text = (
                    f"⏰ یادآوری تقویم فردا ({format_jalali_date(tomorrow_iso)})\n"
                    f"📌 {holiday_type_label(hol['holiday_type'])}: {hol['title']}"
                )
                self.announcements.enqueue_messages(recipients, text, message_type="EVENT_REMINDER")
                executed.append(slot_key)

        # ۴. یادآوری ثبت حضور و غیاب به دبیران در روزهای آموزشی غیرتعطیل
        weekday = tehran_weekday_index(now_dt.date())
        reminder_hm = self.settings.get("attendance_reminder_hour", "09:30")
        current_hm = now_dt.strftime("%H:%M")
        if (
            weekday in self.settings.get_teaching_days()
            and not self.holidays.find_closed_holiday_on(today_str)
            and current_hm >= reminder_hm
        ):
            if self.settings.claim_task_slot("daily_attendance_reminder", "ATTENDANCE_REMINDER", today_str):
                teachers = self.users.search(role="TEACHER", active_only=True, limit=300)
                pending_teachers = []
                for t in teachers:
                    t_classes = self.classes.list_teacher_classes(t["id"])
                    if not t_classes:
                        continue
                    recs = self.attendance.list_records(recorded_by=t["id"], date_from=today_str, date_to=today_str, limit=1)
                    if not recs:
                        pending_teachers.append({"chat_id": t["bale_user_id"], "user_id": t["id"]})
                if pending_teachers:
                    self.announcements.enqueue_messages(
                        pending_teachers,
                        f"📝 یادآوری آموزشی: لطفاً وضعیت حضور و غیاب کلاس‌های امروز ({format_jalali_date(today_str)}) را ثبت نمایید.",
                        message_type="ATTENDANCE_REMINDER",
                    )
                executed.append(f"attendance_reminder:{len(pending_teachers)}")

        # ۵. یادآوری هفتگی ثبت نمرات به دبیران
        grade_rem_day = self.settings.get_int("grade_reminder_day", 4)
        year_week_slot = now_dt.strftime("%Y-W%W")
        if weekday == grade_rem_day and current_hm >= "10:00":
            if self.settings.claim_task_slot("weekly_grade_reminder", "GRADE_REMINDER", year_week_slot):
                teachers = self.users.search(role="TEACHER", active_only=True, limit=300)
                recipients = [{"chat_id": t["bale_user_id"], "user_id": t["id"]} for t in teachers]
                if recipients:
                    self.announcements.enqueue_messages(
                        recipients,
                        "📊 یادآوری هفتگی: در صورت برگزاری آزمون یا ارزشیابی مستمر، نمرات دانش‌آموزان را در سامانه ثبت فرمایید.",
                        message_type="GRADE_REMINDER",
                    )
                executed.append(f"grade_reminder:{len(recipients)}")

        return {"executed": executed}
