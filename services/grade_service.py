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
        actor: Dict[str, Any],
        student_user_id: int,
        academic_term: Optional[str] = None,
    ) -> Dict[str, Any]:
        """محاسبه دقیق کارنامه دانش‌آموز و معدل کل وزنی (بر مبنای ۲۰ با لحاظ وزن ارزیابی‌ها و ضرایب دروس)."""
        self.permissions.ensure_can_access_student(actor, student_user_id)
        student = self.users.get_by_id(student_user_id)
        if not student:
            raise ValidationError("دانش‌آموز یافت نشد.")
        active_cls = self.classes.get_student_active_class(student_user_id)
        items = self.grades.list_grades(student_user_id=student_user_id, academic_term=academic_term, limit=500)

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
            for g in info["items"]:
                ev_w = float(g.get("evaluation_weight") or 1.0)
                norm20 = self._normalize_to_20(float(g["score"]), float(g["min_score"]), float(g["max_score"]))
                w_sum += norm20 * ev_w
                w_total += ev_w
            subj_avg_20 = round(w_sum / w_total, 2) if w_total > 0 else 0.0
            coef = info["coefficient"]
            total_weighted_sum += subj_avg_20 * coef
            total_coefficients += coef
            subject_summaries.append(
                {
                    "subject_id": sid,
                    "subject_name": info["subject_name"],
                    "coefficient": coef,
                    "weighted_average_20": subj_avg_20,
                    "items": info["items"],
                }
            )

        subject_summaries.sort(key=lambda x: x["subject_name"])
        gpa = round(total_weighted_sum / total_coefficients, 2) if total_coefficients > 0 else 0.0
        return {
            "student": student,
            "class_name": active_cls["name"] if active_cls else (items[0]["class_name"] if items else None),
            "term": academic_term,
            "subjects": subject_summaries,
            "gpa": gpa,
        }

    def export_student_report_card(
        self,
        actor: Dict[str, Any],
        student_user_id: int,
        fmt: str = "pdf",
        academic_term: Optional[str] = None,
    ) -> bytes:
        rc = self.get_student_report_card(actor, student_user_id, academic_term=academic_term)
        student = rc["student"]
        title = f"کارنامه آموزشی: {student['full_name']} | کلاس: {rc.get('class_name') or '-'} | معدل کل وزنی: {rc['gpa']:.2f} از 20"
        headers = ["نام درس", "ضریب درس", "میانگین وزنی (از ۲۰)", "ریز نمرات ثبت‌شده"]
        rows: List[List[Any]] = []
        for sb in rc["subjects"]:
            details = " | ".join(
                f"{g['evaluation_name']}: {g['score']:g}/{g['max_score']:g}"
                for g in sb["items"]
            )
            rows.append([sb["subject_name"], sb["coefficient"], sb["weighted_average_20"], details or "-"])
        rows.append(["معدل کل وزنی", "-", rc["gpa"], f"دوره: {rc.get('term') or 'کل دوره‌ها'}"])

        if fmt.lower() in {"xlsx", "excel"}:
            return render_table_xlsx(title, headers, rows, sheet_name="کارنامه")
        return render_table_pdf(title, headers, rows, landscape_mode=False)

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
