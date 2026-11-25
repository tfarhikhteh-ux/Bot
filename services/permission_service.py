from __future__ import annotations

from typing import Any, Dict, Optional, Set

from database.db import DatabaseManager
from database.repositories.classes import ClassRepository
from database.repositories.subjects import SubjectRepository
from database.repositories.users import UserRepository

from .audit_service import AuditService


class AccessDeniedError(PermissionError):
    """خطای عدم مجوز دسترسی برای نمایش پیام امن به کاربر و مسدودسازی عملیات."""


ROLE_RANK = {
    "ADMIN": 100,
    "SCHOOL_MANAGER": 80,
    "DEPUTY": 60,
    "ASSOCIATION_MEMBER": 50,
    "TEACHER": 40,
    "PARENT": 20,
    "STUDENT": 10,
}

# مجوزهای انحصاری سطح ADMIN که هیچ نقش دیگری حتی مدیر مدرسه نباید به آن‌ها دست یابد
ADMIN_EXCLUSIVE_PERMISSIONS: Set[str] = {
    "MANAGE_ADMINS",
    "MANAGE_MANAGERS",
    "MANAGE_SECURITY_SETTINGS",
    "MANAGE_MANAGEMENT_GROUP",
    "BACKUP_CREATE",
    "BACKUP_RESTORE",
    "VIEW_SECURITY_AUDIT",
}

MANAGER_PERMISSIONS: Set[str] = {
    "ACCESS_MANAGEMENT_PANEL",
    "MANAGE_USERS",
    "APPROVE_REGISTRATION",
    "DELEGATE_ASSOCIATION_PERMISSIONS",
    "MANAGE_CLASSES",
    "MANAGE_SUBJECTS",
    "MANAGE_TIMETABLE",
    "MANAGE_ATTENDANCE_ALL",
    "OVERRIDE_HOLIDAY_ATTENDANCE",
    "RECORD_DISCIPLINARY",
    "VIEW_STUDENTS_STATUS",
    "MANAGE_GRADES_ALL",
    "MANAGE_EVALUATION_TYPES",
    "MANAGE_ANNOUNCEMENTS",
    "SEND_BROADCAST",
    "MANAGE_HOLIDAYS",
    "MANAGE_SCHOOL_SETTINGS",
    "MANAGE_GROUP_LOCKS",
    "VIEW_REPORTS_ALL",
    "VIEW_AUDIT_LOGS",
    "VIEW_ANNOUNCEMENTS",
    "VIEW_CALENDAR",
}

DEPUTY_PERMISSIONS: Set[str] = {
    "RECORD_ATTENDANCE_ANY",
    "EDIT_ATTENDANCE",
    "RECORD_DISCIPLINARY",
    "VIEW_STUDENTS_STATUS",
    "MANAGE_ANNOUNCEMENTS",
    "MANAGE_GROUP_LOCKS",
    "VIEW_ATTENDANCE_REPORTS",
    "VIEW_ANNOUNCEMENTS",
    "VIEW_CALENDAR",
}

ASSOCIATION_BASE_PERMISSIONS: Set[str] = {
    "VIEW_ASSOCIATION_PANEL",
    "MANAGE_GROUP_LOCKS",
    "VIEW_ANNOUNCEMENTS",
    "VIEW_CALENDAR",
}

# مجوزهایی که ادمین یا مدیر مدرسه می‌تواند به عضو انجمن تفویض کند
ASSOCIATION_DELEGATABLE_PERMISSIONS: Set[str] = {
    "MANAGE_ANNOUNCEMENTS",
    "VIEW_REPORTS_ALL",
    "MANAGE_HOLIDAYS",
}

TEACHER_PERMISSIONS: Set[str] = {
    "VIEW_OWN_CLASSES",
    "VIEW_OWN_SUBJECTS",
    "VIEW_OWN_TIMETABLE",
    "RECORD_CLASS_ATTENDANCE",
    "MANAGE_CLASS_GRADES",
    "MANAGE_CLASS_HOMEWORK",
    "MANAGE_GROUP_LOCKS",
    "VIEW_CLASS_ACADEMIC_REPORT",
    "VIEW_ANNOUNCEMENTS",
    "VIEW_CALENDAR",
}

STUDENT_PERMISSIONS: Set[str] = {
    "VIEW_OWN_TIMETABLE",
    "VIEW_OWN_GRADES",
    "VIEW_OWN_ATTENDANCE",
    "VIEW_OWN_HOMEWORK",
    "VIEW_ANNOUNCEMENTS",
    "VIEW_CALENDAR",
}

PARENT_PERMISSIONS: Set[str] = {
    "VIEW_CHILDREN_INFO",
    "VIEW_CHILDREN_GRADES",
    "VIEW_CHILDREN_ATTENDANCE",
    "VIEW_CHILDREN_TIMETABLE",
    "VIEW_CHILDREN_REPORT",
    "VIEW_ANNOUNCEMENTS",
    "VIEW_CALENDAR",
}

ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    "ADMIN": ADMIN_EXCLUSIVE_PERMISSIONS | MANAGER_PERMISSIONS | DEPUTY_PERMISSIONS,
    "SCHOOL_MANAGER": MANAGER_PERMISSIONS,
    "DEPUTY": DEPUTY_PERMISSIONS,
    "ASSOCIATION_MEMBER": ASSOCIATION_BASE_PERMISSIONS,
    "TEACHER": TEACHER_PERMISSIONS,
    "STUDENT": STUDENT_PERMISSIONS,
    "PARENT": PARENT_PERMISSIONS,
}


class PermissionService:
    """سرویس متمرکز کنترل دسترسی مبتنی بر نقش (RBAC) و مالکیت داده."""

    def __init__(self, db: DatabaseManager, primary_admin_bale_id: Optional[int] = None) -> None:
        self.db = db
        self.primary_admin_bale_id = primary_admin_bale_id
        self.users = UserRepository(db)
        self.classes = ClassRepository(db)
        self.subjects = SubjectRepository(db)
        self.audit = AuditService(db)

    def get_effective_permissions(self, user: Optional[Dict[str, Any]]) -> Set[str]:
        if not user or not user.get("is_active"):
            return set()
        role = (user.get("role") or "").upper()
        base = set(ROLE_PERMISSIONS.get(role, set()))
        if role == "ASSOCIATION_MEMBER":
            extra = set(self.users.get_custom_permissions(user)) & ASSOCIATION_DELEGATABLE_PERMISSIONS
            base |= extra
        # تضمین قطعی: هیچ نقشی غیر از ADMIN هرگز مجوزهای انحصاری ادمین را دریافت نمی‌کند
        if role != "ADMIN":
            base -= ADMIN_EXCLUSIVE_PERMISSIONS
        return base

    def has_permission(self, user: Optional[Dict[str, Any]], permission: str) -> bool:
        return permission.upper() in self.get_effective_permissions(user)

    def require_active_user(self, user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not user:
            raise AccessDeniedError("شما هنوز در سامانه ثبت‌نام نشده‌اید. از دستور /start استفاده کنید.")
        if not user.get("is_active"):
            raise AccessDeniedError("حساب کاربری شما غیرفعال است. با مدیریت مدرسه تماس بگیرید.")
        return user

    def require_permission(
        self,
        actor: Optional[Dict[str, Any]],
        permission: str,
        entity_type: str = "SYSTEM",
        entity_id: Optional[str | int] = None,
        ip_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        checked_actor = self.require_active_user(actor)
        if not self.has_permission(checked_actor, permission):
            self.audit.log(
                action=f"PERMISSION_DENIED:{permission}",
                entity_type=entity_type,
                actor=checked_actor,
                entity_id=entity_id,
                result="DENIED",
                ip_address=ip_address,
                description=f"تلاش غیرمجاز نقش {checked_actor.get('role')} برای دسترسی به {permission}",
            )
            raise AccessDeniedError("شما مجوز لازم برای انجام این عملیات را ندارید.")
        return checked_actor

    def require_roles(self, actor: Optional[Dict[str, Any]], *allowed_roles: str) -> Dict[str, Any]:
        checked = self.require_active_user(actor)
        allowed_upper = {r.upper() for r in allowed_roles}
        if checked["role"].upper() not in allowed_upper:
            self.audit.log(
                action="ROLE_ACCESS_DENIED",
                entity_type="RBAC",
                actor=checked,
                result="DENIED",
                description=f"نیاز به یکی از نقش‌های {sorted(allowed_upper)}",
            )
            raise AccessDeniedError("این بخش برای نقش کاربری شما مجاز نیست.")
        return checked

    def ensure_can_manage_user(
        self,
        actor: Optional[Dict[str, Any]],
        target_user: Optional[Dict[str, Any]] = None,
        target_new_role: Optional[str] = None,
    ) -> None:
        checked = self.require_active_user(actor)
        actor_role = checked["role"].upper()

        if actor_role not in {"ADMIN", "SCHOOL_MANAGER"}:
            self.audit.log(
                action="USER_MANAGE_DENIED",
                entity_type="USER",
                actor=checked,
                entity_id=target_user["id"] if target_user else None,
                result="DENIED",
                description="کاربر غیرمدیریتی تلاش به مدیریت کاربران کرد.",
            )
            raise AccessDeniedError("فقط ادمین یا مدیر مدرسه مجاز به مدیریت کاربران است.")

        if target_user and checked["id"] == target_user["id"] and target_new_role and target_new_role.upper() != actor_role:
            self.audit.log(
                action="SELF_ROLE_CHANGE_DENIED",
                entity_type="USER",
                actor=checked,
                entity_id=checked["id"],
                result="DENIED",
                description="جلوگیری از تغییر خودسرانه نقش کاربری توسط خود فرد.",
            )
            raise AccessDeniedError("هیچ کاربری مجاز به تغییر نقش خودش نیست.")

        if target_user and self.primary_admin_bale_id and target_user.get("bale_user_id") == self.primary_admin_bale_id:
            if target_new_role and target_new_role.upper() != "ADMIN":
                raise AccessDeniedError("تغییر نقش ادمین اصلی سامانه مجاز نیست.")

        if actor_role == "SCHOOL_MANAGER":
            if target_user and target_user["role"].upper() in {"ADMIN", "SCHOOL_MANAGER"}:
                self.audit.log(
                    action="PRIVILEGE_ESCALATION_BLOCKED",
                    entity_type="USER",
                    actor=checked,
                    entity_id=target_user["id"],
                    result="DENIED",
                    description="تلاش مدیر مدرسه برای مدیریت ادمین یا هم‌سطح خود مسدود شد.",
                )
                raise AccessDeniedError("مدیر مدرسه مجاز به تغییر یا حذف ادمین و مدیران مدرسه نیست.")
            if target_new_role and target_new_role.upper() in {"ADMIN", "SCHOOL_MANAGER"}:
                self.audit.log(
                    action="PRIVILEGE_ESCALATION_BLOCKED",
                    entity_type="USER",
                    actor=checked,
                    result="DENIED",
                    description=f"تلاش مدیر مدرسه برای تخصیص نقش {target_new_role} مسدود شد.",
                )
                raise AccessDeniedError("فقط ادمین اصلی مجاز به ایجاد یا تعیین نقش ادمین و مدیر مدرسه است.")

    def ensure_can_access_student(self, actor: Optional[Dict[str, Any]], student_user_id: int) -> None:
        checked = self.require_active_user(actor)
        role = checked["role"].upper()
        if role in {"ADMIN", "SCHOOL_MANAGER", "DEPUTY"}:
            return
        if role == "STUDENT" and checked["id"] == student_user_id:
            return
        if role == "PARENT":
            children_ids = {c["id"] for c in self.users.get_children_of_parent(checked["id"])}
            if student_user_id in children_ids:
                return
        if role == "TEACHER":
            active_cls = self.classes.get_student_active_class(student_user_id)
            if active_cls and self.subjects.is_teacher_assigned(checked["id"], active_cls["id"]):
                return

        self.audit.log(
            action="STUDENT_DATA_ACCESS_DENIED",
            entity_type="USER",
            actor=checked,
            entity_id=student_user_id,
            result="DENIED",
            description="تلاش برای مشاهده اطلاعات دانش‌آموز خارج از محدوده مجاز.",
        )
        raise AccessDeniedError("شما اجازه دسترسی به اطلاعات این دانش‌آموز را ندارید.")

    def ensure_can_access_class(self, actor: Optional[Dict[str, Any]], class_id: int) -> None:
        checked = self.require_active_user(actor)
        role = checked["role"].upper()
        if role in {"ADMIN", "SCHOOL_MANAGER", "DEPUTY"}:
            return
        if role == "TEACHER" and self.subjects.is_teacher_assigned(checked["id"], class_id):
            return
        if role == "STUDENT":
            cls = self.classes.get_student_active_class(checked["id"])
            if cls and cls["id"] == class_id:
                return
        if role == "PARENT":
            for child in self.users.get_children_of_parent(checked["id"]):
                cls = self.classes.get_student_active_class(child["id"])
                if cls and cls["id"] == class_id:
                    return
        self.audit.log(
            action="CLASS_ACCESS_DENIED",
            entity_type="CLASS",
            actor=checked,
            entity_id=class_id,
            result="DENIED",
            description="تلاش برای دسترسی به کلاس غیرمرتبط مسدود شد.",
        )
        raise AccessDeniedError("شما به اطلاعات این کلاس دسترسی ندارید.")

    def ensure_teacher_class_subject(
        self,
        actor: Optional[Dict[str, Any]],
        class_id: int,
        subject_id: Optional[int] = None,
    ) -> None:
        checked = self.require_active_user(actor)
        role = checked["role"].upper()
        if role in {"ADMIN", "SCHOOL_MANAGER"}:
            return
        if role == "DEPUTY" and subject_id is None:
            return
        if role == "TEACHER" and self.subjects.is_teacher_assigned(checked["id"], class_id, subject_id):
            return
        self.audit.log(
            action="TEACHER_SCOPE_DENIED",
            entity_type="CLASS_SUBJECT",
            actor=checked,
            entity_id=f"{class_id}:{subject_id}",
            result="DENIED",
            description="تلاش دبیر یا کاربر برای عملیات روی کلاس/درس تخصیص‌نیافته.",
        )
        raise AccessDeniedError("این کلاس یا درس به شما تخصیص داده نشده است.")
