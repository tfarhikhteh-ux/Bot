from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import DatabaseManager
from database.repositories.announcements import AnnouncementRepository
from database.repositories.attendance import AttendanceRepository
from database.repositories.classes import ClassRepository
from database.repositories.holidays import HolidayRepository
from database.repositories.settings import SettingsRepository
from database.repositories.users import UserRepository
from utils.dates import format_jalali_date, today_iso
from utils.formatters import attendance_label
from utils.validators import (
    VALID_ATTENDANCE_STATUSES,
    ValidationError,
    sanitize_text,
    validate_date,
    validate_period_number,
)

from .audit_service import AuditService
from .permission_service import AccessDeniedError, PermissionService


class AttendanceService:
    """مدیریت حضور و غیاب، کنترل تعطیلات، اصلاح با ثبت علت، گزارش‌های دوره‌ای و اطلاع به والدین."""

    def __init__(self, db: DatabaseManager, permissions: PermissionService) -> None:
        self.db = db
        self.permissions = permissions
        self.attendance = AttendanceRepository(db)
        self.classes = ClassRepository(db)
        self.users = UserRepository(db)
        self.holidays = HolidayRepository(db)
        self.settings = SettingsRepository(db)
        self.announcements = AnnouncementRepository(db)
        self.audit = AuditService(db)

    def _check_actor_record_permission(self, actor: Dict[str, Any], class_id: int) -> None:
        checked = self.permissions.require_active_user(actor)
        role = checked["role"].upper()
        if role in {"ADMIN", "SCHOOL_MANAGER", "DEPUTY"}:
            return
        if role == "TEACHER":
            self.permissions.ensure_teacher_class_subject(checked, class_id)
            return
        raise AccessDeniedError("شما مجوز ثبت حضور و غیاب ندارید.")

    def record_attendance(
        self,
        actor: Dict[str, Any],
        class_id: int,
        student_user_id: int,
        status: str,
        att_date: Optional[str] = None,
        period_number: int = 1,
        note: Optional[str] = None,
        override_holiday: bool = False,
    ) -> Dict[str, Any]:
        self._check_actor_record_permission(actor, class_id)

        norm_status = (status or "").strip().upper()
        if norm_status not in VALID_ATTENDANCE_STATUSES:
            raise ValidationError("وضعیت حضور و غیاب انتخاب‌شده معتبر نیست.")

        iso_date = validate_date(att_date) if att_date else today_iso()
        p_num = validate_period_number(period_number)
        clean_note = sanitize_text(note, 250, allow_empty=True, field_label="توضیحات") or None

        # بررسی تعطیلات رسمی/مدرسه‌ای/اضطراری
        closed_holiday = self.holidays.find_closed_holiday_on(iso_date)
        if closed_holiday and self.settings.get_bool("strict_holiday_attendance_lock", True):
            if not override_holiday:
                raise ValidationError(
                    f"تاریخ {format_jalali_date(iso_date)} به علت «{closed_holiday['title']}» تعطیل است. "
                    "ثبت حضور و غیاب عادی در روز تعطیل بدون مجوز ویژه امکان‌پذیر نیست."
                )
            self.permissions.require_permission(actor, "OVERRIDE_HOLIDAY_ATTENDANCE", "ATTENDANCE", class_id)

        cls = self.classes.get_by_id(class_id)
        if not cls or not cls["is_active"]:
            raise ValidationError("کلاس انتخاب‌شده فعال نیست.")

        student = self.users.get_by_id(student_user_id)
        if not student or student["role"] != "STUDENT" or not student["is_active"]:
            raise ValidationError("دانش‌آموز غیرفعال یا نامعتبر در حضور و غیاب فعال مجاز نیست.")

        active_students_ids = {s["id"] for s in self.classes.list_class_students(class_id, active_users_only=True)}
        if student_user_id not in active_students_ids:
            raise ValidationError("این دانش‌آموز در فهرست فعال این کلاس قرار ندارد.")

        try:
            record = self.attendance.create(
                class_id=class_id,
                student_user_id=student_user_id,
                att_date=iso_date,
                period_number=p_num,
                status=norm_status,
                recorded_by=actor["id"],
                note=clean_note,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        self.audit.log(
            action="RECORD_ATTENDANCE",
            entity_type="ATTENDANCE",
            actor=actor,
            entity_id=record["id"],
            after_data=record,
            description=f"ثبت وضعیت {norm_status} برای {student['full_name']} در کلاس {cls['name']}",
        )

        if norm_status != "PRESENT" and self.settings.get_bool("notify_parents_attendance", True):
            self._notify_parents_of_attendance(student, cls["name"], iso_date, p_num, norm_status, clean_note)

        return record

    def update_attendance(
        self,
        actor: Dict[str, Any],
        attendance_id: int,
        new_status: str,
        reason: str,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        current = self.attendance.get_by_id(attendance_id)
        if not current:
            raise ValidationError("رکورد حضور و غیاب موردنظر یافت نشد.")

        role = self.permissions.require_active_user(actor)["role"].upper()
        if role == "TEACHER":
            self.permissions.ensure_teacher_class_subject(actor, current["class_id"])
            if current["recorded_by"] != actor["id"]:
                raise AccessDeniedError("دبیر فقط می‌تواند رکوردهای ثبت‌شده توسط خودش را اصلاح کند.")
        elif role not in {"ADMIN", "SCHOOL_MANAGER", "DEPUTY"}:
            raise AccessDeniedError("شما مجوز اصلاح حضور و غیاب را ندارید.")

        norm_status = (new_status or "").strip().upper()
        if norm_status not in VALID_ATTENDANCE_STATUSES:
            raise ValidationError("وضعیت جدید حضور و غیاب معتبر نیست.")
        clean_reason = sanitize_text(reason, 250, field_label="علت اصلاح وضعیت")
        clean_note = sanitize_text(note, 250, allow_empty=True) or None

        updated = self.attendance.update_status(
            attendance_id=attendance_id,
            new_status=norm_status,
            changed_by=actor["id"],
            reason=clean_reason,
            note=clean_note,
        )
        self.audit.log(
            action="UPDATE_ATTENDANCE",
            entity_type="ATTENDANCE",
            actor=actor,
            entity_id=attendance_id,
            before_data={"status": current["status"]},
            after_data={"status": norm_status, "reason": clean_reason},
            description=f"اصلاح حضور و غیاب {current['student_name']} به علت: {clean_reason}",
        )

        student = self.users.get_by_id(current["student_user_id"])
        if student and self.settings.get_bool("notify_parents_attendance", True):
            self._notify_parents_of_attendance(
                student,
                current["class_name"],
                current["att_date"],
                current["period_number"],
                norm_status,
                f"اصلاح وضعیت ({clean_reason})",
            )
        return updated

    def _notify_parents_of_attendance(
        self,
        student: Dict[str, Any],
        class_name: str,
        iso_date: str,
        period_number: int,
        status: str,
        note: Optional[str],
    ) -> None:
        parents = self.users.get_parents_of_student(student["id"])
        if not parents:
            return
        msg = (
            f"🔔 اطلاع‌رسانی وضعیت حضور و غیاب فرزند شما\n"
            f"👤 دانش‌آموز: {student['full_name']}\n"
            f"🏫 کلاس: {class_name}\n"
            f"📅 تاریخ: {format_jalali_date(iso_date)} | زنگ {period_number}\n"
            f"📌 وضعیت: {attendance_label(status)}"
        )
        if note:
            msg += f"\n📝 توضیحات: {note}"
        recipients = [{"chat_id": p["bale_user_id"], "user_id": p["id"]} for p in parents]
        self.announcements.enqueue_messages(recipients, msg, message_type="PARENT_ATTENDANCE_ALERT")

    def get_student_attendance_report(
        self,
        actor: Dict[str, Any],
        student_user_id: int,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.permissions.ensure_can_access_student(actor, student_user_id)
        d_from = validate_date(date_from) if date_from else None
        d_to = validate_date(date_to) if date_to else None
        records = self.attendance.list_records(student_user_id=student_user_id, date_from=d_from, date_to=d_to)
        summary = self.attendance.summarize_status_counts(student_user_id=student_user_id, date_from=d_from, date_to=d_to)
        return {"records": records, "summary": summary}

    def get_class_attendance_report(
        self,
        actor: Dict[str, Any],
        class_id: int,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.permissions.ensure_can_access_class(actor, class_id)
        role = actor["role"].upper()
        if role in {"STUDENT", "PARENT"}:
            raise AccessDeniedError("دانش‌آموز و ولی فقط به گزارش فردی مجاز دسترسی دارند.")
        d_from = validate_date(date_from) if date_from else None
        d_to = validate_date(date_to) if date_to else None
        records = self.attendance.list_records(class_id=class_id, date_from=d_from, date_to=d_to)
        summary = self.attendance.summarize_status_counts(class_id=class_id, date_from=d_from, date_to=d_to)
        return {"records": records, "summary": summary}

    def get_period_summary_report(
        self,
        actor: Dict[str, Any],
        date_from: str,
        date_to: str,
        teacher_user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        role = self.permissions.require_active_user(actor)["role"].upper()
        if role not in {"ADMIN", "SCHOOL_MANAGER", "DEPUTY"}:
            if role == "TEACHER" and teacher_user_id == actor["id"]:
                pass
            else:
                raise AccessDeniedError("مشاهده گزارش کلی حضور و غیاب برای نقش شما مجاز نیست.")
        d_from = validate_date(date_from)
        d_to = validate_date(date_to)
        records = self.attendance.list_records(recorded_by=teacher_user_id, date_from=d_from, date_to=d_to, limit=500)
        summary = self.attendance.summarize_status_counts(recorded_by=teacher_user_id, date_from=d_from, date_to=d_to)
        return {"records": records, "summary": summary, "date_from": d_from, "date_to": d_to}

    def record_disciplinary_note(
        self,
        actor: Dict[str, Any],
        student_user_id: int,
        category: str,
        description: str,
        note_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "RECORD_DISCIPLINARY", "DISCIPLINARY", student_user_id)
        student = self.users.get_by_id(student_user_id)
        if not student or student["role"] != "STUDENT":
            raise ValidationError("دانش‌آموز معتبر یافت نشد.")
        iso_date = validate_date(note_date) if note_date else today_iso()
        cat = sanitize_text(category, 60, field_label="نوع گزارش انضباطی")
        desc = sanitize_text(description, 400, field_label="شرح گزارش انضباطی")
        cls = self.classes.get_student_active_class(student_user_id)
        item = self.attendance.add_disciplinary_note(
            student_user_id=student_user_id,
            class_id=cls["id"] if cls else None,
            note_date=iso_date,
            category=cat,
            description=desc,
            recorded_by=actor["id"],
        )
        self.audit.log(
            action="RECORD_DISCIPLINARY",
            entity_type="DISCIPLINARY",
            actor=actor,
            entity_id=item["id"],
            after_data=item,
            description=f"ثبت گزارش انضباطی برای {student['full_name']}",
        )
        return item
