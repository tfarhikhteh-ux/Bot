from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import DatabaseManager
from database.repositories.classes import ClassRepository
from database.repositories.settings import SettingsRepository
from database.repositories.subjects import SubjectRepository
from database.repositories.timetable import TimetableRepository
from database.repositories.users import UserRepository
from utils.dates import PERSIAN_WEEKDAYS, tehran_weekday_index
from utils.formatters import format_timetable_text, render_table_pdf, render_table_xlsx, render_timetable_png, week_type_label
from utils.validators import (
     VALID_SUBJECT_TYPES,
    ValidationError,
    sanitize_text,
    validate_academic_year,
    validate_grade_level,
    validate_period_number,
    validate_positive_float,
    validate_positive_int,
    validate_time_range,
    validate_week_type,
    validate_weekday,
)

from .audit_service import AuditService
from .permission_service import PermissionService


class TimetableService:
    """مدیریت یکپارچه کلاس‌ها، درس‌ها، زنگ‌های مدرسه و برنامه هفتگی (هفته اول و دوم)."""

    def __init__(self, db: DatabaseManager, permissions: PermissionService) -> None:
        self.db = db
        self.permissions = permissions
        self.classes = ClassRepository(db)
        self.subjects = SubjectRepository(db)
        self.timetable = TimetableRepository(db)
        self.users = UserRepository(db)
        self.settings = SettingsRepository(db)
        self.audit = AuditService(db)

    # ---------- مدیریت کلاس‌ها ----------
    def create_class(
        self,
        actor: Dict[str, Any],
        name: str,
        grade_level: str,
        academic_year: Optional[str] = None,
        capacity: int = 35,
    ) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "MANAGE_CLASSES", "CLASS")
        clean_name = sanitize_text(name, 40, field_label="نام کلاس")
        clean_grade = validate_grade_level(grade_level)
        clean_year = validate_academic_year(academic_year or self.settings.get("academic_year", "1405-1406"))
        clean_cap = validate_positive_int(capacity, "ظرفیت کلاس", 1, 200)
        if self.classes.get_by_name_and_year(clean_name, clean_year):
            raise ValidationError("کلاسی با این نام در همین سال تحصیلی وجود دارد.")
        cls = self.classes.create(clean_name, clean_grade, clean_year, clean_cap, changed_by=actor["id"])
        self.audit.log(
            action="CREATE_CLASS",
            entity_type="CLASS",
            actor=actor,
            entity_id=cls["id"],
            after_data=cls,
            description=f"ایجاد کلاس {clean_name} پایه {clean_grade}",
        )
        return cls

    def update_class(
        self,
        actor: Dict[str, Any],
        class_id: int,
        name: Optional[str] = None,
        grade_level: Optional[str] = None,
        academic_year: Optional[str] = None,
        capacity: Optional[int] = None,
        is_active: Optional[bool] = None,
    ) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "MANAGE_CLASSES", "CLASS", class_id)
        before = self.classes.get_by_id(class_id)
        if not before:
            raise ValidationError("کلاس موردنظر یافت نشد.")
        updated = self.classes.update(
            class_id=class_id,
            name=sanitize_text(name, 40, field_label="نام کلاس") if name is not None else None,
            grade_level=validate_grade_level(grade_level) if grade_level is not None else None,
            academic_year=validate_academic_year(academic_year) if academic_year is not None else None,
            capacity=validate_positive_int(capacity, "ظرفیت", 1, 200) if capacity is not None else None,
            is_active=(1 if is_active else 0) if is_active is not None else None,
            changed_by=actor["id"],
        )
        self.audit.log(
            action="UPDATE_CLASS",
            entity_type="CLASS",
            actor=actor,
            entity_id=class_id,
            before_data=before,
            after_data=updated,
            description=f"ویرایش کلاس {updated['name']}",
        )
        return updated

    def delete_class_controlled(self, actor: Dict[str, Any], class_id: int) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "MANAGE_CLASSES", "CLASS", class_id)
        before = self.classes.get_by_id(class_id)
        if not before:
            raise ValidationError("کلاس موردنظر یافت نشد.")
        res = self.classes.controlled_delete_or_archive(class_id, changed_by=actor["id"])
        self.audit.log(
            action="DELETE_OR_ARCHIVE_CLASS",
            entity_type="CLASS",
            actor=actor,
            entity_id=class_id,
            before_data=before,
            after_data=res,
            description=f"حذف کنترل‌شده کلاس {before['name']} ({res['mode']})",
        )
        return res

    def enroll_student_in_class(self, actor: Dict[str, Any], class_id: int, student_user_id: int) -> None:
        self.permissions.require_permission(actor, "MANAGE_CLASSES", "CLASS", class_id)
        student = self.users.get_by_id(student_user_id)
        if not student or student["role"] != "STUDENT" or not student["is_active"]:
            raise ValidationError("کاربر انتخاب‌شده دانش‌آموز فعال نیست.")
        try:
            self.classes.enroll_student(class_id, student_user_id, changed_by=actor["id"])
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        self.audit.log(
            action="ENROLL_STUDENT",
            entity_type="CLASS_ENROLLMENT",
            actor=actor,
            entity_id=f"{class_id}:{student_user_id}",
            description=f"ثبت دانش‌آموز {student['full_name']} در کلاس #{class_id}",
        )

    def transfer_student_between_classes(
        self,
        actor: Dict[str, Any],
        student_user_id: int,
        to_class_id: int,
        note: str = "",
    ) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "MANAGE_CLASSES", "CLASS", to_class_id)
        student = self.users.get_by_id(student_user_id)
        if not student or student["role"] != "STUDENT":
            raise ValidationError("کاربر موردنظر دانش‌آموز نیست.")
        try:
            res = self.classes.transfer_student(student_user_id, to_class_id, changed_by=actor["id"], note=note)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        self.audit.log(
            action="TRANSFER_STUDENT",
            entity_type="CLASS_ENROLLMENT",
            actor=actor,
            entity_id=student_user_id,
            after_data=res,
            description=f"انتقال دانش‌آموز {student['full_name']} به کلاس #{to_class_id}",
        )
        return res

    # ---------- مدیریت درس‌ها ----------
    def create_subject(
        self,
        actor: Dict[str, Any],
        name: str,
        grade_level: str = "عمومی",
        subject_type: str = "عمومی",
        code: Optional[str] = None,
        default_weekly_periods: int = 2,
        default_coefficient: float = 1.0,
    ) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "MANAGE_SUBJECTS", "SUBJECT")
        clean_name = sanitize_text(name, 60, field_label="نام درس")
        clean_grade = sanitize_text(grade_level, 30, field_label="پایه درس")
        s_type = subject_type.strip() if subject_type in VALID_SUBJECT_TYPES else "عمومی"
        periods = validate_positive_int(default_weekly_periods, "تعداد زنگ هفتگی", 1, 20)
        coef = validate_positive_float(default_coefficient, "ضریب درس", 10.0)
        try:
            subj = self.subjects.create(clean_name, clean_grade, s_type, code, periods, coef)
        except Exception as exc:
            raise ValidationError("درسی با این نام و پایه یا کد قبلاً ثبت شده است.") from exc
        self.audit.log(
            action="CREATE_SUBJECT",
            entity_type="SUBJECT",
            actor=actor,
            entity_id=subj["id"],
            after_data=subj,
            description=f"ایجاد درس {clean_name}",
        )
        return subj

    def update_subject(
        self,
        actor: Dict[str, Any],
        subject_id: int,
        name: Optional[str] = None,
        grade_level: Optional[str] = None,
        subject_type: Optional[str] = None,
        default_weekly_periods: Optional[int] = None,
        default_coefficient: Optional[float] = None,
    ) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "MANAGE_SUBJECTS", "SUBJECT", subject_id)
        before = self.subjects.get_by_id(subject_id)
        if not before:
            raise ValidationError("درس موردنظر یافت نشد.")
        fields: Dict[str, Any] = {}
        if name is not None:
            fields["name"] = sanitize_text(name, 60, field_label="نام درس")
        if grade_level is not None:
            fields["grade_level"] = sanitize_text(grade_level, 30, field_label="پایه درس")
        if subject_type is not None and subject_type in VALID_SUBJECT_TYPES:
            fields["subject_type"] = subject_type
        if default_weekly_periods is not None:
            fields["default_weekly_periods"] = validate_positive_int(default_weekly_periods, "زنگ هفتگی", 1, 20)
        if default_coefficient is not None:
            fields["default_coefficient"] = validate_positive_float(default_coefficient, "ضریب", 10.0)
        updated = self.subjects.update(subject_id, **fields)
        self.audit.log(
            action="UPDATE_SUBJECT",
            entity_type="SUBJECT",
            actor=actor,
            entity_id=subject_id,
            before_data=before,
            after_data=updated,
            description=f"ویرایش درس {updated['name']}",
        )
        return updated

    def delete_subject_controlled(self, actor: Dict[str, Any], subject_id: int) -> str:
        self.permissions.require_permission(actor, "MANAGE_SUBJECTS", "SUBJECT", subject_id)
        before = self.subjects.get_by_id(subject_id)
        if not before:
            raise ValidationError("درس موردنظر یافت نشد.")
        mode = self.subjects.controlled_delete(subject_id)
        self.audit.log(
            action="DELETE_SUBJECT",
            entity_type="SUBJECT",
            actor=actor,
            entity_id=subject_id,
            before_data=before,
            after_data={"mode": mode},
            description=f"حذف کنترل‌شده درس {before['name']} ({mode})",
        )
        return mode

    def assign_subject_to_class(
        self,
        actor: Dict[str, Any],
        class_id: int,
        subject_id: int,
        teacher_user_id: Optional[int],
        weekly_periods: int = 2,
        coefficient: Optional[float] = None,
    ) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "MANAGE_SUBJECTS", "CLASS_SUBJECT", f"{class_id}:{subject_id}")
        cls = self.classes.get_by_id(class_id)
        subj = self.subjects.get_by_id(subject_id)
        if not cls or not cls["is_active"]:
            raise ValidationError("کلاس معتبر و فعال نیست.")
        if not subj or not subj["is_active"]:
            raise ValidationError("درس معتبر و فعال نیست.")
        if teacher_user_id is not None:
            teacher = self.users.get_by_id(teacher_user_id)
            if not teacher or teacher["role"] != "TEACHER" or not teacher["is_active"]:
                raise ValidationError("کاربر انتخاب‌شده دبیر فعال نیست.")
        periods = validate_positive_int(weekly_periods, "تعداد زنگ هفتگی", 1, 20)
        coef = validate_positive_float(coefficient if coefficient is not None else subj["default_coefficient"], "ضریب درس", 10.0)
        try:
            assigned = self.subjects.assign_to_class(class_id, subject_id, teacher_user_id, periods, coef)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        self.audit.log(
            action="ASSIGN_CLASS_SUBJECT",
            entity_type="CLASS_SUBJECT",
            actor=actor,
            entity_id=assigned["id"],
            after_data=assigned,
            description=f"تخصیص درس {subj['name']} به کلاس {cls['name']}",
        )
        return assigned

    # ---------- مدیریت زنگ‌ها و برنامه هفتگی ----------
    def configure_bell_period(
        self,
        actor: Dict[str, Any],
        period_number: int,
        title: str,
        start_time: str,
        end_time: str,
    ) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "MANAGE_TIMETABLE", "BELL_PERIOD", period_number)
        p_num = validate_period_number(period_number)
        clean_title = sanitize_text(title, 40, field_label="عنوان زنگ")
        s_t, e_t = validate_time_range(start_time, end_time)
        try:
            period = self.timetable.upsert_bell_period(p_num, clean_title, s_t, e_t)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        self.audit.log(
            action="CONFIGURE_BELL_PERIOD",
            entity_type="BELL_PERIOD",
            actor=actor,
            entity_id=p_num,
            after_data=period,
            description=f"تنظیم {clean_title} ({s_t}-{e_t})",
        )
        return period

    def set_timetable_slot(
        self,
        actor: Dict[str, Any],
        class_id: int,
        day_of_week: int | str,
        period_number: int | str,
        week_type: str,
        subject_id: int,
        teacher_user_id: Optional[int] = None,
        room: Optional[str] = None,
        replace_existing: bool = False,
    ) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "MANAGE_TIMETABLE", "TIMETABLE", class_id)
        cls = self.classes.get_by_id(class_id)
        if not cls or not cls["is_active"]:
            raise ValidationError("کلاس انتخاب‌شده فعال نیست.")
        subj = self.subjects.get_by_id(subject_id)
        if not subj or not subj["is_active"]:
            raise ValidationError("درس انتخاب‌شده فعال نیست.")

        day_idx = validate_weekday(day_of_week)
        p_num = validate_period_number(period_number)
        w_type = validate_week_type(week_type)

        if not self.timetable.get_bell_period(p_num):
            raise ValidationError(f"زنگ شماره {p_num} هنوز در سیستم تعریف نشده است.")

        # اگر دبیر صریح داده نشده باشد، دبیر تخصیص‌یافته آن درس در آن کلاس استفاده می‌شود
        if teacher_user_id is None:
            for cs in self.subjects.list_class_subjects(class_id):
                if cs["subject_id"] == subject_id and cs.get("teacher_user_id"):
                    teacher_user_id = cs["teacher_user_id"]
                    break
        else:
            teacher = self.users.get_by_id(teacher_user_id)
            if not teacher or teacher["role"] != "TEACHER" or not teacher["is_active"]:
                raise ValidationError("دبیر انتخاب‌شده معتبر یا فعال نیست.")

        try:
            entry = self.timetable.set_slot(
                class_id=class_id,
                day_of_week=day_idx,
                period_number=p_num,
                week_type=w_type,
                subject_id=subject_id,
                teacher_user_id=teacher_user_id,
                updated_by=actor["id"],
                room=room,
                replace_existing=replace_existing,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        self.audit.log(
            action="SET_TIMETABLE_SLOT",
            entity_type="TIMETABLE",
            actor=actor,
            entity_id=entry["id"],
            after_data=entry,
            description=f"ثبت برنامه کلاس {cls['name']} - {PERSIAN_WEEKDAYS[day_idx]} زنگ {p_num} ({w_type})",
        )
        return entry

    def clear_timetable_slot(
        self,
        actor: Dict[str, Any],
        class_id: int,
        day_of_week: int | str,
        period_number: int | str,
        week_type: Optional[str] = None,
    ) -> int:
        self.permissions.require_permission(actor, "MANAGE_TIMETABLE", "TIMETABLE", class_id)
        day_idx = validate_weekday(day_of_week)
        p_num = validate_period_number(period_number)
        wt = validate_week_type(week_type) if week_type and week_type.upper() != "ALL" else "ALL"
        deleted = self.timetable.clear_slot(class_id, day_idx, p_num, wt, changed_by=actor["id"])
        self.audit.log(
            action="CLEAR_TIMETABLE_SLOT",
            entity_type="TIMETABLE",
            actor=actor,
            entity_id=f"{class_id}:{day_idx}:{p_num}",
            after_data={"deleted": deleted, "week_type": wt},
            description=f"پاک‌کردن خانه برنامه کلاس #{class_id}",
        )
        return deleted

    def apply_quick_edit_command(self, actor: Dict[str, Any], raw_args_text: str) -> str:
        """
        پشتیبانی از دستور سریع مدیریتی:
        /برنامه_ویرایش کلاس | روز | زنگ | هفته | درس
        اگر نام درس «حذف» یا «خالی» باشد خانه برنامه پاک می‌شود.
        """
        self.permissions.require_permission(actor, "MANAGE_TIMETABLE", "TIMETABLE")
        parts = [p.strip() for p in (raw_args_text or "").split("|")]
        if len(parts) != 5:
            raise ValidationError(
                "ساختار دستور نادرست است.\n"
                "الگوی صحیح:\n"
                "/برنامه_ویرایش نام کلاس | روز | شماره زنگ | نوع هفته | نام درس"
            )
        cls_name, day_raw, period_raw, week_raw, subj_name = parts
        year = self.settings.get("academic_year", "1405-1406")
        cls = self.classes.get_by_name_and_year(cls_name, year)
        if not cls:
            candidates = [c for c in self.classes.list_classes(query=cls_name, active_only=True) if c["name"] == cls_name]
            cls = candidates[0] if candidates else None
        if not cls:
            raise ValidationError(f"کلاس «{cls_name}» یافت نشد.")

        if subj_name in {"حذف", "خالی", "-"}:
            cnt = self.clear_timetable_slot(actor, cls["id"], day_raw, period_raw, week_raw)
            return f"✅ خانه برنامه کلاس «{cls['name']}» پاک شد ({cnt} مورد)."

        subjects = [s for s in self.subjects.list_subjects(active_only=True) if s["name"] == subj_name]
        if not subjects:
            raise ValidationError(f"درس «{subj_name}» در فهرست دروس فعال یافت نشد.")
        entry = self.set_timetable_slot(
            actor=actor,
            class_id=cls["id"],
            day_of_week=day_raw,
            period_number=period_raw,
            week_type=week_raw,
            subject_id=subjects[0]["id"],
            replace_existing=True,
        )
        return (
            f"✅ برنامه هفتگی با موفقیت به‌روزرسانی شد:\n"
            f"🏫 کلاس {cls['name']} | {PERSIAN_WEEKDAYS[entry['day_of_week']]} زنگ {entry['period_number']} "
            f"({week_type_label(entry['week_type'])}) ← {entry['subject_name']}"
        )

    def get_formatted_class_timetable(
        self,
        actor: Dict[str, Any],
        class_id: int,
        week_type: Optional[str] = None,
        daily_only: bool = False,
    ) -> str:
        self.permissions.ensure_can_access_class(actor, class_id)
        cls = self.classes.get_by_id(class_id)
        if not cls:
            raise ValidationError("کلاس یافت نشد.")
        wt = validate_week_type(week_type) if week_type else None
        day_filter = tehran_weekday_index() if daily_only else None
        entries = self.timetable.get_class_timetable(class_id, week_type=wt, day_of_week=day_filter)
        periods = self.timetable.list_bell_periods()
        days = [day_filter] if daily_only and day_filter is not None else self.settings.get_teaching_days()
        wk_txt = f" ({week_type_label(wt)})" if wt else " (کامل: هفته اول و دوم)"
        mode_txt = "برنامه امروز" if daily_only else "برنامه هفتگی"
        return format_timetable_text(f"{mode_txt} کلاس {cls['name']}{wk_txt}", entries, periods, days, for_teacher=False)

    def get_formatted_teacher_timetable(
        self,
        actor: Dict[str, Any],
        teacher_user_id: Optional[int] = None,
        week_type: Optional[str] = None,
        daily_only: bool = False,
    ) -> str:
        target_id = teacher_user_id or actor["id"]
        if target_id != actor["id"]:
            self.permissions.require_permission(actor, "MANAGE_TIMETABLE", "TIMETABLE", target_id)
        else:
            self.permissions.require_permission(actor, "VIEW_OWN_TIMETABLE", "TIMETABLE", target_id)
        teacher = self.users.get_by_id(target_id)
        if not teacher:
            raise ValidationError("دبیر موردنظر یافت نشد.")
        wt = validate_week_type(week_type) if week_type else None
        day_filter = tehran_weekday_index() if daily_only else None
        entries = self.timetable.get_teacher_timetable(target_id, week_type=wt, day_of_week=day_filter)
        periods = self.timetable.list_bell_periods()
        days = [day_filter] if daily_only and day_filter is not None else self.settings.get_teaching_days()
        wk_txt = f" ({week_type_label(wt)})" if wt else ""
        return format_timetable_text(f"برنامه تدریس {teacher['full_name']}{wk_txt}", entries, periods, days, for_teacher=True)

    def export_class_timetable_png(self, actor: Dict[str, Any], class_id: int, week_type: Optional[str] = None) -> bytes:
        self.permissions.ensure_can_access_class(actor, class_id)
        cls = self.classes.get_by_id(class_id)
        if not cls:
            raise ValidationError("کلاس یافت نشد.")
        wt = validate_week_type(week_type) if week_type else None
        entries = self.timetable.get_class_timetable(class_id, week_type=wt)
        periods = self.timetable.list_bell_periods()
        days = self.settings.get_teaching_days()
        wk_txt = f" - {week_type_label(wt)}" if wt else ""
        return render_timetable_png(f"برنامه هفتگی کلاس {cls['name']}{wk_txt}", entries, periods, days, for_teacher=False)

    def export_class_timetable_pdf(self, actor: Dict[str, Any], class_id: int, week_type: Optional[str] = None) -> bytes:
        self.permissions.ensure_can_access_class(actor, class_id)
        cls = self.classes.get_by_id(class_id)
        if not cls:
            raise ValidationError("کلاس یافت نشد.")
        wt = validate_week_type(week_type) if week_type else None
        entries = self.timetable.get_class_timetable(class_id, week_type=wt)
        periods = self.timetable.list_bell_periods()
        days = self.settings.get_teaching_days()
        headers = ["روز هفته"] + [f"زنگ {p['period_number']} ({p['start_time']}-{p['end_time']})" for p in periods]
        lookup = {}
        for e in entries:
            wk = "" if e["week_type"] == "BOTH" else f" [{week_type_label(e['week_type'])}]"
            lookup.setdefault((e["day_of_week"], e["period_number"]), []).append(f"{e['subject_name']}{wk}")
        rows = []
        for d in days:
            row = [PERSIAN_WEEKDAYS.get(d, str(d))]
            for p in periods:
                row.append(" / ".join(lookup.get((d, p["period_number"]), ["-"])))
            rows.append(row)
        wk_txt = f" ({week_type_label(wt)})" if wt else ""
        return render_table_pdf(f"برنامه هفتگی کلاس {cls['name']}{wk_txt}", headers, rows, landscape_mode=True)

    def export_class_timetable_xlsx(self, actor: Dict[str, Any], class_id: int, week_type: Optional[str] = None) -> bytes:
        self.permissions.ensure_can_access_class(actor, class_id)
        cls = self.classes.get_by_id(class_id)
        if not cls:
            raise ValidationError("کلاس یافت نشد.")
        wt = validate_week_type(week_type) if week_type else None
        entries = self.timetable.get_class_timetable(class_id, week_type=wt)
        periods = self.timetable.list_bell_periods()
        days = self.settings.get_teaching_days()
        headers = ["روز هفته"] + [f"زنگ {p['period_number']} ({p['start_time']}-{p['end_time']})" for p in periods]
        lookup = {}
        for e in entries:
            wk = "" if e["week_type"] == "BOTH" else f" [{week_type_label(e['week_type'])}]"
            t_txt = f" ({e['teacher_name']})" if e.get("teacher_name") else ""
            lookup.setdefault((e["day_of_week"], e["period_number"]), []).append(f"{e['subject_name']}{t_txt}{wk}")
        rows = []
        for d in days:
            row = [PERSIAN_WEEKDAYS.get(d, str(d))]
            for p in periods:
                row.append(" / ".join(lookup.get((d, p["period_number"]), ["-"])))
            rows.append(row)
        wk_txt = f" ({week_type_label(wt)})" if wt else ""
        return render_table_xlsx(f"برنامه هفتگی کلاس {cls['name']}{wk_txt}", headers, rows, sheet_name=f"کلاس {cls['name']}")
