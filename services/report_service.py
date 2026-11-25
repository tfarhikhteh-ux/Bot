from __future__ import annotations

import csv
import io
from typing import Any, Dict, List, Optional, Sequence

from database.db import DatabaseManager
from database.repositories.attendance import AttendanceRepository
from database.repositories.audit import AuditRepository
from database.repositories.classes import ClassRepository
from database.repositories.grades import GradeRepository
from database.repositories.registration import RegistrationRepository
from database.repositories.subjects import SubjectRepository
from database.repositories.timetable import TimetableRepository
from database.repositories.users import UserRepository
from utils.dates import format_jalali_date, format_jalali_datetime
from utils.formatters import attendance_label, render_table_pdf, role_label
from utils.validators import validate_date

from .grade_service import GradeService
from .permission_service import PermissionService


class ReportService:
    """تولید گزارش‌های جامع مدیریتی، آماری، عملکردی و خروجی‌های CSV و PDF."""

    def __init__(
        self,
        db: DatabaseManager,
        permissions: PermissionService,
        grade_service: GradeService,
    ) -> None:
        self.db = db
        self.permissions = permissions
        self.grade_service = grade_service
        self.users = UserRepository(db)
        self.classes = ClassRepository(db)
        self.subjects = SubjectRepository(db)
        self.attendance = AttendanceRepository(db)
        self.grades = GradeRepository(db)
        self.registrations = RegistrationRepository(db)
        self.timetable = TimetableRepository(db)
        self.audit = AuditRepository(db)

    def get_dashboard_overview(
        self,
        actor: Dict[str, Any],
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        class_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "VIEW_REPORTS_ALL", "REPORT")
        d_from = validate_date(date_from) if date_from else None
        d_to = validate_date(date_to) if date_to else None

        role_counts = self.users.count_by_role(active_only=True)
        all_users_count = sum(role_counts.values())
        active_classes = self.classes.list_classes(active_only=True)
        att_summary = self.attendance.summarize_status_counts(class_id=class_id, date_from=d_from, date_to=d_to)
        pending_regs = len(self.registrations.list_requests(status="PENDING", limit=500))

        class_gpa_list = []
        target_classes = [c for c in active_classes if class_id is None or c["id"] == class_id]
        for cls in target_classes:
            perf = self.grade_service.get_class_performance_report(actor, cls["id"])
            class_gpa_list.append(
                {
                    "class_id": cls["id"],
                    "class_name": cls["name"],
                    "grade_level": cls["grade_level"],
                    "student_count": cls["active_student_count"],
                    "gpa": perf["class_gpa"],
                }
            )

        teacher_stats = self.get_teacher_performance_summary(actor, date_from=d_from, date_to=d_to)

        return {
            "total_users": all_users_count,
            "students_count": role_counts.get("STUDENT", 0),
            "teachers_count": role_counts.get("TEACHER", 0),
            "parents_count": role_counts.get("PARENT", 0),
            "deputies_count": role_counts.get("DEPUTY", 0),
            "managers_count": role_counts.get("SCHOOL_MANAGER", 0),
            "classes_count": len(active_classes),
            "pending_registrations": pending_regs,
            "role_counts": role_counts,
            "attendance_summary": att_summary,
            "class_gpas": class_gpa_list,
            "teacher_performance": teacher_stats,
        }

    def get_teacher_performance_summary(
        self,
        actor: Dict[str, Any],
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        self.permissions.require_permission(actor, "VIEW_REPORTS_ALL", "REPORT")
        teachers = self.users.search(role="TEACHER", active_only=True, limit=300)
        result = []
        for t in teachers:
            assigned = self.subjects.list_teacher_subjects(t["id"])
            att_counts = self.attendance.summarize_status_counts(
                recorded_by=t["id"], date_from=date_from, date_to=date_to
            )
            t_grades = self.grades.list_grades(teacher_user_id=t["id"], limit=1000)
            avg_score = round(sum(float(g["score"]) for g in t_grades) / len(t_grades), 2) if t_grades else 0.0
            result.append(
                {
                    "teacher_id": t["id"],
                    "full_name": t["full_name"],
                    "assigned_courses": len(assigned),
                    "attendance_recorded": att_counts["TOTAL"],
                    "grades_recorded": len(t_grades),
                    "average_raw_score": avg_score,
                }
            )
        return result

    def get_change_reports(
        self,
        actor: Dict[str, Any],
        report_kind: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        self.permissions.require_permission(actor, "VIEW_AUDIT_LOGS", "REPORT", report_kind)
        d_from = validate_date(date_from) if date_from else None
        d_to = validate_date(date_to) if date_to else None
        kind = report_kind.upper()
        if kind == "ROLE_CHANGES":
            return self.audit.search(action="CHANGE_USER_ROLE", date_from=d_from, date_to=d_to, limit=limit)
        if kind == "GRADE_CHANGES":
            return self.grades.list_history(limit=limit)
        if kind == "TIMETABLE_CHANGES":
            return self.timetable.list_history(limit=limit)
        if kind == "MANAGEMENT_OPS":
            return self.audit.search(date_from=d_from, date_to=d_to, limit=limit)
        raise ValueError("نوع گزارش درخواستی نامعتبر است.")

    @staticmethod
    def build_csv_bytes(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(headers)
        for r in rows:
            writer.writerow(["" if v is None else str(v) for v in r])
        # افزودن UTF-8 BOM برای نمایش صحیح فارسی در نرم‌افزار Excel در Windows
        return ("\ufeff" + buf.getvalue()).encode("utf-8")

    def export_report(
        self,
        actor: Dict[str, Any],
        category: str,
        fmt: str = "csv",
        class_id: Optional[int] = None,
        subject_id: Optional[int] = None,
        role: Optional[str] = None,
        user_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> bytes:
        self.permissions.require_permission(actor, "VIEW_REPORTS_ALL", "REPORT_EXPORT", category)
        d_from = validate_date(date_from) if date_from else None
        d_to = validate_date(date_to) if date_to else None
        cat = category.upper()

        if cat == "USERS":
            items = self.users.search(role=role, active_only=False, limit=500)
            headers = ["شناسه", "نام و نام خانوادگی", "شناسه بله", "نقش", "پایه", "کد دانش‌آموزی", "وضعیت"]
            rows = [
                [
                    u["id"],
                    u["full_name"],
                    u["bale_user_id"],
                    role_label(u["role"]),
                    u.get("grade_level") or "-",
                    u.get("student_code") or "-",
                    "فعال" if u["is_active"] else "غیرفعال",
                ]
                for u in items
            ]
            title = "گزارش کاربران سامانه مدرسه"
        elif cat == "ATTENDANCE":
            items = self.attendance.list_records(
                class_id=class_id,
                student_user_id=user_id,
                date_from=d_from,
                date_to=d_to,
                limit=500,
            )
            headers = ["تاریخ", "زنگ", "کلاس", "دانش‌آموز", "وضعیت", "ثبت‌کننده", "توضیحات"]
            rows = [
                [
                    format_jalali_date(a["att_date"], persian_digits=False),
                    a["period_number"],
                    a["class_name"],
                    a["student_name"],
                    attendance_label(a["status"]),
                    a["recorder_name"],
                    a.get("note") or "-",
                ]
                for a in items
            ]
            title = "گزارش حضور و غیاب مدرسه"
        elif cat == "GRADES":
            items = self.grades.list_grades(
                student_user_id=user_id,
                class_id=class_id,
                subject_id=subject_id,
                limit=500,
            )
            headers = ["دانش‌آموز", "کلاس", "درس", "ارزیابی", "دوره", "نمره", "سقف نمره", "ضریب"]
            rows = [
                [
                    g["student_name"],
                    g["class_name"],
                    g["subject_name"],
                    g["evaluation_name"],
                    g["academic_term"],
                    g["score"],
                    g["max_score"],
                    g["coefficient"],
                ]
                for g in items
            ]
            title = "گزارش نمرات و عملکرد تحصیلی"
        elif cat == "AUDIT":
            items = self.audit.search(actor_user_id=user_id, date_from=d_from, date_to=d_to, limit=300)
            headers = ["زمان", "کاربر", "عملیات", "موضوع", "نتیجه", "توضیحات"]
            rows = [
                [
                    format_jalali_datetime(a["created_at"], persian_digits=False),
                    a.get("actor_name") or a.get("actor_bale_id") or "سیستم",
                    a["action"],
                    a["entity_type"],
                    a["result"],
                    a.get("description") or "-",
                ]
                for a in items
            ]
            title = "گزارش فعالیت‌ها و ممیزی امنیتی"
        else:
            overview = self.get_dashboard_overview(actor, date_from=d_from, date_to=d_to, class_id=class_id)
            headers = ["شاخص مدیریتی", "مقدار"]
            rows = [
                ["تعداد کل کاربران فعال", overview["total_users"]],
                ["تعداد دانش‌آموزان فعال", overview["students_count"]],
                ["تعداد دبیران فعال", overview["teachers_count"]],
                ["تعداد کلاس‌های فعال", overview["classes_count"]],
                ["درخواست‌های ثبت‌نام در انتظار", overview["pending_registrations"]],
                ["کل رکوردهای حضور و غیاب", overview["attendance_summary"]["TOTAL"]],
                ["مجموع غیبت‌ها", overview["attendance_summary"]["ABSENT"] + overview["attendance_summary"]["UNEXCUSED_ABSENT"] + overview["attendance_summary"]["EXCUSED_ABSENT"]],
            ]
            title = "خلاصه گزارش مدیریتی مدرسه"

        if fmt.lower() == "pdf":
            return render_table_pdf(title, headers, rows, landscape_mode=(len(headers) > 5))
        return self.build_csv_bytes(headers, rows)
