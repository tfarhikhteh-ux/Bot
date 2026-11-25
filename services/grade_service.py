from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import DatabaseManager
from database.repositories.announcements import AnnouncementRepository
from database.repositories.classes import ClassRepository
from database.repositories.grades import GradeRepository
from database.repositories.settings import SettingsRepository
from database.repositories.subjects import SubjectRepository
from database.repositories.users import UserRepository
from utils.formatters import render_table_pdf, render_table_xlsx
from utils.validators import (
    ValidationError,
    sanitize_text,
    validate_date,
    validate_positive_float,
    validate_score,
)

from .audit_service import AuditService
from .permission_service import AccessDeniedError, PermissionService


class AmbiguousTeacherSubjectError(ValidationError):
    """زمانی که یک دبیر در یک کلاس بیش از یک درس تدریس می‌کند و باید درس موردنظر برای مثبت/منفی درسی انتخاب شود."""

    def __init__(
        self,
        student: Dict[str, Any],
        cls: Dict[str, Any],
        subjects: List[Dict[str, Any]],
        delta: float,
    ) -> None:
        super().__init__("شما در این کلاس بیش از یک درس دارید؛ لطفاً درس موردنظر را انتخاب کنید.")
        self.student = student
        self.cls = cls
        self.subjects = subjects
        self.delta = delta


class GradeService:
    """مدیریت انواع ارزیابی، نمرات، محاسبه دقیق معدل وزنی، کارنامه، تکالیف و اطلاع‌رسانی والدین."""

    def __init__(self, db: DatabaseManager, permissions: PermissionService) -> None:
        self.db = db
        self.permissions = permissions
        self.grades = GradeRepository(db)
        self.classes = ClassRepository(db)
        self.subjects = SubjectRepository(db)
        self.users = UserRepository(db)
        self.settings = SettingsRepository(db)
        self.announcements = AnnouncementRepository(db)
        self.audit = AuditService(db)

    def create_evaluation_type(
        self,
        actor: Dict[str, Any],
        name: str,
        term: Optional[str] = None,
        min_score: float = 0.0,
        max_score: float = 20.0,
        weight: float = 1.0,
    ) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "MANAGE_EVALUATION_TYPES", "EVALUATION_TYPE")
        clean_name = sanitize_text(name, 60, field_label="عنوان نوع ارزیابی")
        clean_term = sanitize_text(term or self.settings.get("current_term", "ترم اول"), 30, field_label="دوره آموزشی")
        min_s = float(min_score)
        max_s = float(max_score)
        if max_s <= min_s:
            raise ValidationError("سقف نمره باید بزرگ‌تر از کف نمره باشد.")
        w = validate_positive_float(weight, "ضریب وزن ارزیابی", 20.0)
        try:
            et = self.grades.create_evaluation_type(clean_name, clean_term, min_s, max_s, w)
        except Exception as exc:
            raise ValidationError("نوع ارزیابی با این نام قبلاً ثبت شده است.") from exc
        self.audit.log(
            action="CREATE_EVALUATION_TYPE",
            entity_type="EVALUATION_TYPE",
            actor=actor,
            entity_id=et["id"],
            after_data=et,
            description=f"تعریف نوع ارزیابی {clean_name} ({min_s} تا {max_s})",
        )
        return et

    def record_grade(
        self,
        actor: Dict[str, Any],
        student_user_id: int,
        class_id: int,
        subject_id: int,
        evaluation_type_id: int,
        score: float | str,
        academic_term: Optional[str] = None,
        coefficient: Optional[float] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.permissions.ensure_teacher_class_subject(actor, class_id, subject_id)
        cls = self.classes.get_by_id(class_id)
        subj = self.subjects.get_by_id(subject_id)
        ev = self.grades.get_evaluation_type(evaluation_type_id)
        student = self.users.get_by_id(student_user_id)

        if not cls or not cls["is_active"]:
            raise ValidationError("کلاس انتخاب‌شده فعال نیست.")
        if not subj or not subj["is_active"]:
            raise ValidationError("درس انتخاب‌شده فعال نیست.")
        if not ev or not ev["is_active"]:
            raise ValidationError("نوع ارزیابی معتبر نیست.")
        if not student or student["role"] != "STUDENT" or not student["is_active"]:
            raise ValidationError("دانش‌آموز انتخاب‌شده معتبر و فعال نیست.")

        enrolled_ids = {s["id"] for s in self.classes.list_class_students(class_id, active_users_only=True)}
        if student_user_id not in enrolled_ids:
            raise ValidationError("این دانش‌آموز عضو فعال این کلاس نیست.")

        valid_score = validate_score(score, ev["min_score"], ev["max_score"])
        term = sanitize_text(academic_term or ev["term"] or self.settings.get("current_term", "ترم اول"), 30)

        # ضریب درس از تخصیص کلاس یا ضریب پیش‌فرض درس خوانده می‌شود
        resolved_coef = coefficient
        teacher_id = actor["id"] if actor["role"] == "TEACHER" else None
        for cs in self.subjects.list_class_subjects(class_id):
            if cs["subject_id"] == subject_id:
                if resolved_coef is None:
                    resolved_coef = cs["coefficient"]
                if teacher_id is None:
                    teacher_id = cs.get("teacher_user_id")
                break
        final_coef = validate_positive_float(
            resolved_coef if resolved_coef is not None else subj["default_coefficient"],
            "ضریب درس",
            20.0,
        )
        clean_note = sanitize_text(note, 250, allow_empty=True) or None

        try:
            grade = self.grades.create_grade(
                student_user_id=student_user_id,
                class_id=class_id,
                subject_id=subject_id,
                teacher_user_id=teacher_id,
                evaluation_type_id=evaluation_type_id,
                academic_term=term,
                score=valid_score,
                min_score=ev["min_score"],
                max_score=ev["max_score"],
                coefficient=final_coef,
                recorded_by=actor["id"],
                note=clean_note,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        self.audit.log(
            action="RECORD_GRADE",
            entity_type="GRADE",
            actor=actor,
            entity_id=grade["id"],
            after_data=grade,
            description=f"ثبت نمره {valid_score} درس {subj['name']} برای {student['full_name']}",
        )

        if self.settings.get_bool("notify_parents_grades", True):
            self._notify_parents_of_grade(student, subj["name"], ev["name"], valid_score, ev["max_score"])
        return grade

    def update_grade(
        self,
        actor: Dict[str, Any],
        grade_id: int,
        new_score: float | str,
        reason: str = "اصلاح نمره",
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        current = self.grades.get_by_id(grade_id)
        if not current:
            raise ValidationError("نمره موردنظر یافت نشد.")
        self.permissions.ensure_teacher_class_subject(actor, current["class_id"], current["subject_id"])
        valid_score = validate_score(new_score, current["min_score"], current["max_score"])
        clean_reason = sanitize_text(reason, 250, field_label="علت تغییر نمره")
        updated = self.grades.update_grade(grade_id, valid_score, changed_by=actor["id"], reason=clean_reason, note=note)
        self.audit.log(
            action="UPDATE_GRADE",
            entity_type="GRADE",
            actor=actor,
            entity_id=grade_id,
            before_data={"score": current["score"]},
            after_data={"score": valid_score, "reason": clean_reason},
            description=f"ویرایش نمره {current['student_name']} در درس {current['subject_name']} ({current['score']} -> {valid_score})",
        )
        student = self.users.get_by_id(current["student_user_id"])
        if student and self.settings.get_bool("notify_parents_grades", True):
            self._notify_parents_of_grade(
                student,
                current["subject_name"],
                f"{current['evaluation_name']} (اصلاح‌شده)",
                valid_score,
                current["max_score"],
            )
        return updated

    def delete_grade_controlled(self, actor: Dict[str, Any], grade_id: int, reason: str = "حذف کنترل‌شده") -> Dict[str, Any]:
        current = self.grades.get_by_id(grade_id)
        if not current:
            raise ValidationError("نمره موردنظر یافت نشد.")
        self.permissions.ensure_teacher_class_subject(actor, current["class_id"], current["subject_id"])
        clean_reason = sanitize_text(reason, 250, field_label="علت حذف نمره")
        deleted = self.grades.soft_delete_grade(grade_id, changed_by=actor["id"], reason=clean_reason)
        self.audit.log(
            action="DELETE_GRADE",
            entity_type="GRADE",
            actor=actor,
            entity_id=grade_id,
            before_data={"score": current["score"]},
            after_data={"is_deleted": 1, "reason": clean_reason},
            description=f"حذف کنترل‌شده نمره #{grade_id}",
        )
        return deleted

    def _notify_parents_of_grade(
        self,
        student: Dict[str, Any],
        subject_name: str,
        eval_name: str,
        score: float,
        max_score: float,
    ) -> None:
        parents = self.users.get_parents_of_student(student["id"])
        if not parents:
            return
        msg = (
            f"📊 ثبت نمره جدید برای فرزند شما\n"
            f"👤 دانش‌آموز: {student['full_name']}\n"
            f"📘 درس: {subject_name}\n"
            f"📝 ارزیابی: {eval_name}\n"
            f"⭐ نمره کسب‌شده: {score:g} از {max_score:g}"
        )
        recipients = [{"chat_id": p["bale_user_id"], "user_id": p["id"]} for p in parents]
        self.announcements.enqueue_messages(recipients, msg, message_type="PARENT_GRADE_ALERT")

    @staticmethod
    def _normalize_to_20(score: float, min_score: float, max_score: float) -> float:
        span = max_score - min_score
        if span <= 0:
            return 0.0
        return ((score - min_score) / span) * 20.0

    def get_student_report_card(
        self,
        actor: Optional[Dict[str, Any]],
        student_user_id: int,
        academic_term: Optional[str] = None,
        trusted_token_access: bool = False,
    ) -> Dict[str, Any]:
        """محاسبه دقیق کارنامه آنلاین دانش‌آموز (تفکیک مستمر، پایانی، مثبت/منفی درسی و معدل کل وزنی از ۲۰)."""
        if not trusted_token_access:
            checked = self.permissions.require_active_user(actor)
            self.permissions.ensure_can_access_student(checked, student_user_id)
            if checked["role"].upper() in {"STUDENT", "PARENT"} and not self.settings.get_bool("online_report_card_enabled", True):
                raise ValidationError("نمایش کارنامه آنلاین در حال حاضر توسط مدیریت مدرسه موقتاً غیرفعال شده است.")
        else:
            if not self.settings.get_bool("online_report_card_enabled", True):
                raise ValidationError("نمایش کارنامه آنلاین در حال حاضر توسط مدیریت مدرسه موقتاً غیرفعال شده است.")

        student = self.users.get_by_id(student_user_id)
        if not student:
            raise ValidationError("دانش‌آموز یافت نشد.")
        active_cls = self.classes.get_student_active_class(student_user_id)
        items = self.grades.list_grades(student_user_id=student_user_id, academic_term=academic_term, limit=500)
        mod_summary = self.grades.get_student_modifier_summary(student_user_id, academic_term=academic_term)
        recent_mods = self.grades.list_student_modifiers(student_user_id, academic_term=academic_term, limit=20)

        grouped: Dict[int, Dict[str, Any]] = {}
        for g in items:
            sid = g["subject_id"]
            if sid not in grouped:
                grouped[sid] = {
                    "subject_id": sid,
                    "subject_name": g["subject_name"],
                    "coefficient": float(g["coefficient"]),
                    "items": [],
                }
            grouped[sid]["items"].append(g)

        subject_summaries: List[Dict[str, Any]] = []
        total_weighted_sum = 0.0
        total_coefficients = 0.0

        for sid, info in grouped.items():
            w_sum = 0.0
            w_total = 0.0
            continuous_score: Optional[float] = None
            final_score: Optional[float] = None
            for g in info["items"]:
                ev_name = str(g.get("evaluation_name") or "")
                ev_w = float(g.get("evaluation_weight") or 1.0)
                norm20 = self._normalize_to_20(float(g["score"]), float(g["min_score"]), float(g["max_score"]))
                w_sum += norm20 * ev_w
                w_total += ev_w
                if "مستمر" in ev_name and "پایان" not in ev_name and continuous_score is None:
                    continuous_score = float(g["score"])
                elif ("پایان" in ev_name or "نهایی" in ev_name) and final_score is None:
                    final_score = float(g["score"])
            subj_avg_20 = round(w_sum / w_total, 2) if w_total > 0 else 0.0
            coef = info["coefficient"]
            total_weighted_sum += subj_avg_20 * coef
            total_coefficients += coef
            m_stat = mod_summary.get(sid, {"positive_count": 0, "negative_count": 0, "net_delta": 0.0})
            subject_summaries.append(
                {
                    "subject_id": sid,
                    "subject_name": info["subject_name"],
                    "coefficient": coef,
                    "continuous_score": continuous_score,
                    "final_score": final_score,
                    "positive_count": m_stat["positive_count"],
                    "negative_count": m_stat["negative_count"],
                    "net_delta": m_stat["net_delta"],
                    "weighted_average_20": subj_avg_20,
                    "items": info["items"],
                }
            )

        subject_summaries.sort(key=lambda x: x["subject_name"])
        gpa = round(total_weighted_sum / total_coefficients, 2) if total_coefficients > 0 else 0.0
        return {
            "student": student,
            "class_name": active_cls["name"] if active_cls else (items[0]["class_name"] if items else None),
            "school_name": self.settings.get("school_name", "مدرسه"),
            "academic_year": self.settings.get("academic_year", "1405-1406"),
            "term": academic_term,
            "subjects": subject_summaries,
            "recent_modifiers": recent_mods,
            "gpa": gpa,
        }

    def export_student_report_card(
        self,
        actor: Optional[Dict[str, Any]],
        student_user_id: int,
        fmt: str = "pdf",
        academic_term: Optional[str] = None,
        trusted_token_access: bool = False,
    ) -> bytes:
        rc = self.get_student_report_card(
            actor,
            student_user_id,
            academic_term=academic_term,
            trusted_token_access=trusted_token_access,
        )
        student = rc["student"]
        title = f"کارنامه آنلاین آموزشی: {student['full_name']} | کلاس: {rc.get('class_name') or '-'} | معدل کل وزنی: {rc['gpa']:.2f} از 20"
        headers = ["نام درس", "ضریب", "نمره مستمر", "مثبت / منفی درسی", "نمره پایانی", "میانگین وزنی (از ۲۰)", "سایر ریز نمرات"]
        rows: List[List[Any]] = []
        for sb in rc["subjects"]:
            mod_txt = f"+{sb['positive_count']} / -{sb['negative_count']}"
            details = " | ".join(
                f"{g['evaluation_name']}: {g['score']:g}/{g['max_score']:g}"
                for g in sb["items"]
            )
            rows.append(
                [
                    sb["subject_name"],
                    sb["coefficient"],
                    sb["continuous_score"] if sb["continuous_score"] is not None else "-",
                    mod_txt,
                    sb["final_score"] if sb["final_score"] is not None else "-",
                    sb["weighted_average_20"],
                    details or "-",
                ]
            )
        rows.append(["معدل کل وزنی", "-", "-", "-", "-", rc["gpa"], f"دوره: {rc.get('term') or 'کل دوره‌ها'}"])

        if fmt.lower() in {"xlsx", "excel"}:
            return render_table_xlsx(title, headers, rows, sheet_name="کارنامه")
        return render_table_pdf(title, headers, rows, landscape_mode=True)

    def apply_continuous_modifier(
        self,
        actor: Optional[Dict[str, Any]],
        student_user_id: int,
        delta: float = 1.0,
        subject_id: Optional[int] = None,
        subject_hint: Optional[str] = None,
        note: Optional[str] = None,
        source_chat_id: Optional[int] = None,
        source_message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        اعمال «مثبت درسی» (+1) یا «منفی درسی» (-1) با ریپلای دبیر روی پیام دانش‌آموز:
        - فقط روی «نمره مستمر» همان درسِ تخصیص‌یافته به همان دبیر اعمال می‌شود.
        - هرگز به نمره «پایانی» یا «میان‌ترم» دست نمی‌زند.
        """
        checked = self.permissions.require_active_user(actor)
        role = checked["role"].upper()
        if role not in {"ADMIN", "SCHOOL_MANAGER", "DEPUTY", "TEACHER", "ASSOCIATION_MEMBER"}:
            self.audit.log(
                action="CONTINUOUS_MODIFIER_DENIED",
                entity_type="GRADE",
                actor=checked,
                entity_id=student_user_id,
                result="DENIED",
                description="تلاش کاربر غیرمجاز برای ثبت مثبت یا منفی درسی",
            )
            raise AccessDeniedError("فقط کارکنان مدرسه (ادمین، مدیر، ناظم، دبیر و انجمن) مجاز به ثبت «مثبت درسی» یا «منفی درسی» هستند.")

        if delta == 0:
            raise ValidationError("مقدار تغییر نمره مستمر نباید صفر باشد.")

        if self.grades.is_modifier_message_processed(source_chat_id, source_message_id):
            raise ValidationError("این پیام مثبت/منفی درسی قبلاً ثبت شده است.")

        student = self.users.get_by_id(student_user_id)
        if not student or student["role"] != "STUDENT" or not student["is_active"]:
            raise ValidationError("کاربر ریپلای‌شده دانش‌آموز فعال سامانه نیست.")

        cls = self.classes.get_student_active_class(student["id"])
        if not cls or not cls["is_active"]:
            raise ValidationError(f"دانش‌آموز «{student['full_name']}» در حال حاضر در هیچ کلاس فعالی ثبت نشده است.")

        all_class_subjects = self.subjects.list_class_subjects(cls["id"])
        if role == "TEACHER":
            candidates = [cs for cs in all_class_subjects if cs.get("teacher_user_id") == checked["id"]]
        else:
            candidates = list(all_class_subjects)

        if not candidates:
            self.audit.log(
                action="TEACHER_MODIFIER_SCOPE_DENIED",
                entity_type="CLASS_SUBJECT",
                actor=checked,
                entity_id=f"{cls['id']}:{student['id']}",
                result="DENIED",
                description="تلاش دبیر غیرمرتبط برای ثبت مثبت/منفی درسی در کلاس دانش‌آموز",
            )
            raise AccessDeniedError(f"شما دبیر هیچ‌یک از دروس کلاس «{cls['name']}» نیستید.")

        chosen_cs: Optional[Dict[str, Any]] = None
        if subject_id is not None:
            for cs in candidates:
                if int(cs["subject_id"]) == int(subject_id):
                    chosen_cs = cs
                    break
            if not chosen_cs:
                raise AccessDeniedError("این درس در کلاس دانش‌آموز به شما تخصیص داده نشده است.")
        elif subject_hint and subject_hint.strip():
            hint = subject_hint.strip()
            exact = [cs for cs in candidates if cs["subject_name"] == hint]
            partial = exact or [cs for cs in candidates if hint in cs["subject_name"]]
            if len(partial) == 1:
                chosen_cs = partial[0]
            elif len(partial) > 1:
                raise AmbiguousTeacherSubjectError(student=student, cls=cls, subjects=partial, delta=delta)
            else:
                names = "، ".join(cs["subject_name"] for cs in candidates)
                raise ValidationError(f"درس «{hint}» در دروس شما برای کلاس {cls['name']} یافت نشد. دروس مجاز شما: {names}")
        elif len(candidates) == 1:
            chosen_cs = candidates[0]
        else:
            raise AmbiguousTeacherSubjectError(student=student, cls=cls, subjects=candidates, delta=delta)

        term = self.settings.get("current_term", "ترم اول")
        cont_eval = self.grades.get_or_create_continuous_eval_type(term)
        if "پایان" in cont_eval["name"] or "نهایی" in cont_eval["name"]:
            raise RuntimeError("خطای ایمنی: نوع ارزیابی انتخاب‌شده نباید پایانی باشد.")

        existing_grade = self.grades.find_student_eval_grade(
            student_user_id=student["id"],
            class_id=cls["id"],
            subject_id=chosen_cs["subject_id"],
            evaluation_type_id=cont_eval["id"],
            academic_term=term,
        )

        min_s = float(cont_eval["min_score"])
        max_s = float(cont_eval["max_score"])
        action_title = "مثبت درسی (+1 مستمر)" if delta > 0 else "منفی درسی (-1 مستمر)"
        clean_note = sanitize_text(note, 200, allow_empty=True) or action_title
        teacher_uid = chosen_cs.get("teacher_user_id") or checked["id"]

        if existing_grade:
            old_score = float(existing_grade["score"])
            new_score = round(max(min_s, min(max_s, old_score + float(delta))), 2)
            grade_row = self.grades.update_grade(
                grade_id=existing_grade["id"],
                new_score=new_score,
                changed_by=checked["id"],
                reason=action_title,
                note=clean_note,
            )
        else:
            try:
                base_score = float(self.settings.get("continuous_base_score", "15") or 15.0)
            except ValueError:
                base_score = 15.0
            old_score = max(min_s, min(max_s, base_score))
            new_score = round(max(min_s, min(max_s, old_score + float(delta))), 2)
            grade_row = self.grades.create_grade(
                student_user_id=student["id"],
                class_id=cls["id"],
                subject_id=chosen_cs["subject_id"],
                teacher_user_id=teacher_uid,
                evaluation_type_id=cont_eval["id"],
                academic_term=term,
                score=new_score,
                min_score=min_s,
                max_score=max_s,
                coefficient=float(chosen_cs["coefficient"]),
                recorded_by=checked["id"],
                note=clean_note,
            )

        self.grades.record_continuous_modifier(
            student_user_id=student["id"],
            class_id=cls["id"],
            subject_id=chosen_cs["subject_id"],
            teacher_user_id=checked["id"],
            academic_term=term,
            delta=float(delta),
            old_score=old_score,
            new_score=new_score,
            note=clean_note,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
        )

        mod_stats = self.grades.get_student_modifier_summary(student["id"], academic_term=term).get(
            int(chosen_cs["subject_id"]),
            {"positive_count": 0, "negative_count": 0, "net_delta": 0.0},
        )

        self.audit.log(
            action="POSITIVE_CLASS_SCORE" if delta > 0 else "NEGATIVE_CLASS_SCORE",
            entity_type="GRADE",
            actor=checked,
            entity_id=grade_row["id"],
            before_data={"continuous_score": old_score},
            after_data={"continuous_score": new_score, "delta": delta, "subject": chosen_cs["subject_name"]},
            description=f"{action_title} برای {student['full_name']} در درس {chosen_cs['subject_name']}",
        )

        if self.settings.get_bool("notify_parents_grades", True):
            badge = "➕ مثبت درسی (+۱ نمره مستمر)" if delta > 0 else "➖ منفی درسی (-۱ نمره مستمر)"
            self._notify_parents_of_grade(
                student,
                chosen_cs["subject_name"],
                f"{badge} — {cont_eval['name']}",
                new_score,
                max_s,
            )

        return {
            "student": student,
            "class_id": cls["id"],
            "class_name": cls["name"],
            "subject_id": chosen_cs["subject_id"],
            "subject_name": chosen_cs["subject_name"],
            "evaluation_name": cont_eval["name"],
            "term": term,
            "delta": float(delta),
            "old_score": old_score,
            "new_score": new_score,
            "max_score": max_s,
            "positive_count": mod_stats["positive_count"],
            "negative_count": mod_stats["negative_count"],
            "net_delta": mod_stats["net_delta"],
            "grade_id": grade_row["id"],
        }

    def get_class_performance_report(
        self,
        actor: Dict[str, Any],
        class_id: int,
        academic_term: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.permissions.ensure_can_access_class(actor, class_id)
        if actor["role"].upper() in {"STUDENT", "PARENT"}:
            raise AccessDeniedError("مشاهده گزارش عملکرد کل کلاس فقط برای کادر آموزشی و مدیریتی مجاز است.")
        cls = self.classes.get_by_id(class_id)
        if not cls:
            raise ValidationError("کلاس یافت نشد.")
        students = self.classes.list_class_students(class_id, active_users_only=True)
        student_gpas = []
        for stu in students:
            rc = self.get_student_report_card(actor, stu["id"], academic_term=academic_term)
            if rc["subjects"]:
                student_gpas.append({"student_id": stu["id"], "full_name": stu["full_name"], "gpa": rc["gpa"]})
        student_gpas.sort(key=lambda x: x["gpa"], reverse=True)
        class_avg = round(sum(x["gpa"] for x in student_gpas) / len(student_gpas), 2) if student_gpas else 0.0
        return {"class": cls, "class_gpa": class_avg, "students": student_gpas}

    def get_subject_performance_report(
        self,
        actor: Dict[str, Any],
        class_id: int,
        subject_id: int,
        academic_term: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.permissions.ensure_teacher_class_subject(actor, class_id, subject_id)
        rows = self.grades.list_grades(class_id=class_id, subject_id=subject_id, academic_term=academic_term)
        scores20 = [self._normalize_to_20(float(r["score"]), float(r["min_score"]), float(r["max_score"])) for r in rows]
        avg_s = round(sum(scores20) / len(scores20), 2) if scores20 else 0.0
        return {
            "count": len(rows),
            "average_20": avg_s,
            "min_20": round(min(scores20), 2) if scores20 else 0.0,
            "max_20": round(max(scores20), 2) if scores20 else 0.0,
            "grades": rows,
        }

    def create_homework(
        self,
        actor: Dict[str, Any],
        class_id: int,
        subject_id: int,
        title: str,
        description: str,
        due_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.permissions.ensure_teacher_class_subject(actor, class_id, subject_id)
        if not self.settings.get_bool("homework_enabled", True):
            raise ValidationError("بخش تکالیف در تنظیمات مدرسه غیرفعال است.")
        clean_title = sanitize_text(title, 80, field_label="عنوان تکلیف")
        clean_desc = sanitize_text(description, 600, field_label="توضیحات تکلیف")
        iso_due = validate_date(due_date) if due_date else None
        hw = self.grades.create_homework(class_id, subject_id, actor["id"], clean_title, clean_desc, iso_due)
        self.audit.log(
            action="CREATE_HOMEWORK",
            entity_type="HOMEWORK",
            actor=actor,
            entity_id=hw["id"],
            after_data=hw,
            description=f"ثبت تکلیف {clean_title}",
        )
        return hw

    def list_class_homework(self, actor: Dict[str, Any], class_id: int) -> List[Dict[str, Any]]:
        self.permissions.ensure_can_access_class(actor, class_id)
        return self.grades.list_homework(class_id)
