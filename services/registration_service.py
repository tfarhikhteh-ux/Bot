from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

from database.db import DatabaseManager
from database.repositories.announcements import AnnouncementRepository
from database.repositories.classes import ClassRepository
from database.repositories.registration import RegistrationRepository
from database.repositories.settings import SettingsRepository
from database.repositories.users import UserRepository
from utils.dates import now_iso, now_tehran
from utils.formatters import role_label
from utils.security import hash_password, verify_password
from utils.validators import (
    ValidationError,
    sanitize_text,
    validate_full_name,
    validate_grade_level,
    validate_phone,
    validate_role,
    validate_student_code,
)

from .audit_service import AuditService
from .permission_service import ASSOCIATION_DELEGATABLE_PERMISSIONS, AccessDeniedError, PermissionService


class RegistrationService:
    """مدیریت کامل ثبت‌نام، تأیید/رد در گروه مدیریتی، مدیریت کاربران و احراز هویت پنل وب."""

    def __init__(
        self,
        db: DatabaseManager,
        permissions: PermissionService,
        management_group_id: Optional[int] = None,
    ) -> None:
        self.db = db
        self.permissions = permissions
        self.users = UserRepository(db)
        self.reg_repo = RegistrationRepository(db)
        self.classes = ClassRepository(db)
        self.settings = SettingsRepository(db)
        self.announcements = AnnouncementRepository(db)
        self.audit = AuditService(db)
        self._fallback_mgmt_group_id = management_group_id

    def get_management_group_id(self) -> Optional[int]:
        raw = self.settings.get("management_group_id", "").strip()
        if raw and raw.lstrip("-").isdigit():
            val = int(raw)
            if val != 0:
                return val
        return self._fallback_mgmt_group_id

    def submit_registration(
        self,
        bale_user_id: int,
        full_name: str,
        requested_role: str,
        phone: Optional[str] = None,
        grade_level: Optional[str] = None,
        requested_class_name: Optional[str] = None,
        student_code: Optional[str] = None,
        parent_phone: Optional[str] = None,
        target_student_code: Optional[str] = None,
        specialty: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.settings.get_bool("registration_open", True):
            raise ValidationError("ثبت‌نام کاربران جدید در حال حاضر توسط مدیریت غیرفعال شده است.")

        existing_user = self.users.get_by_bale_id(bale_user_id)
        if existing_user:
            if existing_user["is_active"]:
                raise ValidationError("شما قبلاً در سامانه ثبت‌نام شده‌اید.")
            raise ValidationError("حساب کاربری قبلی شما غیرفعال است؛ لطفاً با مدیریت مدرسه هماهنگ کنید.")

        pending = self.reg_repo.get_pending_by_bale_id(bale_user_id)
        if pending:
            raise ValidationError("شما یک درخواست ثبت‌نام در انتظار بررسی دارید.")

        role_clean = validate_role(requested_role, self_registration=True)
        name_clean = validate_full_name(full_name)
        phone_clean = validate_phone(phone, required=(role_clean in {"TEACHER", "PARENT", "DEPUTY", "ASSOCIATION_MEMBER"}))
        grade_clean = validate_grade_level(grade_level) if grade_level else None
        cls_clean = sanitize_text(requested_class_name, 40, allow_empty=True, field_label="نام کلاس") or None
        stu_code_clean = validate_student_code(student_code, required=False)
        parent_phone_clean = validate_phone(parent_phone, required=False)
        target_stu_clean = validate_student_code(target_student_code, required=(role_clean == "PARENT"))
        spec_clean = sanitize_text(specialty, 80, allow_empty=True, field_label="رشته / تخصص") or None

        if role_clean == "STUDENT" and not grade_clean:
            raise ValidationError("وارد کردن پایه تحصیلی برای دانش‌آموز الزامی است.")

        if stu_code_clean and self.users.get_by_student_code(stu_code_clean):
            raise ValidationError("این کد دانش‌آموزی قبلاً برای کاربر دیگری ثبت شده است.")

        req = self.reg_repo.create_request(
            bale_user_id=bale_user_id,
            full_name=name_clean,
            requested_role=role_clean,
            phone=phone_clean,
            grade_level=grade_clean,
            requested_class_name=cls_clean,
            student_code=stu_code_clean,
            parent_phone=parent_phone_clean,
            target_student_code=target_stu_clean,
            specialty=spec_clean,
        )

        self.audit.log(
            action="SUBMIT_REGISTRATION",
            entity_type="REGISTRATION_REQUEST",
            actor_bale_id=bale_user_id,
            entity_id=req["id"],
            after_data=req,
            description=f"ثبت درخواست ثبت‌نام جدید با نقش {role_clean}",
        )

        # ارسال گزارش درخواست ثبت‌نام صرفاً به گروه مدیریتی (نه خصوصی مدیران)
        mgmt_group_id = self.get_management_group_id()
        if mgmt_group_id:
            mgmt_text = self.format_management_request_report(req)
            self.announcements.enqueue_messages(
                recipients=[{"chat_id": mgmt_group_id, "user_id": None}],
                text=mgmt_text,
                message_type="REGISTRATION_MANAGEMENT_GROUP",
            )
        return req

    @staticmethod
    def format_management_request_report(req: Dict[str, Any]) -> str:
        lines = [
            "📥 درخواست ثبت‌نام جدید در سامانه مدرسه",
            f"▫️ شماره درخواست: #{req['id']}",
            f"▫️ نام و نام خانوادگی: {req['full_name']}",
            f"▫️ شناسه بله: {req['bale_user_id']}",
            f"▫️ نقش درخواستی: {role_label(req['requested_role'])}",
        ]
        if req.get("phone"):
            lines.append(f"▫️ شماره تماس: {req['phone']}")
        if req.get("grade_level"):
            lines.append(f"▫️ پایه: {req['grade_level']} | کلاس پیشنهادی: {req.get('requested_class_name') or '-'}")
        if req.get("student_code"):
            lines.append(f"▫️ کد دانش‌آموزی: {req['student_code']}")
        if req.get("target_student_code"):
            lines.append(f"▫️ کد دانش‌آموزی فرزند: {req['target_student_code']}")
        if req.get("specialty"):
            lines.append(f"▫️ تخصص / درس: {req['specialty']}")
        lines.append("برای بررسی از منوی «درخواست‌های ثبت‌نام» یا دستورهای مدیریتی استفاده کنید.")
        return "\n".join(lines)

    def approve_registration(
        self,
        actor: Dict[str, Any],
        req_id: int,
        assigned_role: Optional[str] = None,
        class_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "APPROVE_REGISTRATION", "REGISTRATION_REQUEST", req_id)
        req = self.reg_repo.get_by_id(req_id)
        if not req:
            raise ValidationError("درخواست ثبت‌نام یافت نشد.")
        if req["status"] != "PENDING":
            raise ValidationError("این درخواست قبلاً تعیین تکلیف شده است و امکان تأیید مجدد ندارد.")

        final_role = validate_role(assigned_role or req["requested_role"], self_registration=False)
        self.permissions.ensure_can_manage_user(actor, target_user=None, target_new_role=final_role)

        if self.users.get_by_bale_id(req["bale_user_id"]):
            raise ValidationError("کاربری با این شناسه بله هم‌اکنون در سامانه وجود دارد.")

        if not self.reg_repo.transition_status_atomic(req_id, "APPROVED", actor["id"]):
            raise ValidationError("این درخواست هم‌زمان توسط مسئول دیگری بررسی شده است.")

        created_user = self.users.create(
            bale_user_id=req["bale_user_id"],
            full_name=req["full_name"],
            role=final_role,
            phone=req.get("phone"),
            student_code=req.get("student_code"),
            grade_level=req.get("grade_level"),
            parent_phone=req.get("parent_phone"),
            specialty=req.get("specialty"),
            is_active=1,
        )

        # در صورت دانش‌آموز بودن، تخصیص کلاس در صورت مشخص بودن
        target_cls_id = class_id
        if final_role == "STUDENT" and target_cls_id is None and req.get("requested_class_name"):
            year = self.settings.get("academic_year", "1405-1406")
            found_cls = self.classes.get_by_name_and_year(req["requested_class_name"], year)
            if found_cls and found_cls["is_active"]:
                target_cls_id = found_cls["id"]
        if final_role == "STUDENT" and target_cls_id is not None:
            self.classes.enroll_student(target_cls_id, created_user["id"], changed_by=actor["id"])

        # در صورت والد بودن، اتصال خودکار به فرزند با کد دانش‌آموزی
        if final_role == "PARENT" and req.get("target_student_code"):
            child = self.users.get_by_student_code(req["target_student_code"])
            if child and child["role"] == "STUDENT":
                self.users.link_parent_student(created_user["id"], child["id"])

        self.audit.log(
            action="APPROVE_REGISTRATION",
            entity_type="REGISTRATION_REQUEST",
            actor=actor,
            entity_id=req_id,
            after_data={"user_id": created_user["id"], "role": final_role},
            description=f"تأیید درخواست ثبت‌نام #{req_id} برای {created_user['full_name']} توسط {actor['full_name']}",
        )

        # اطلاع به کاربر
        self.announcements.enqueue_messages(
            recipients=[{"chat_id": created_user["bale_user_id"], "user_id": created_user["id"]}],
            text=(
                f"✅ درخواست ثبت‌نام شما در سامانه مدرسه تأیید شد.\n"
                f"👤 نقش تخصیص‌یافته: {role_label(final_role)}\n"
                f"برای مشاهده امکانات، دستور /start را ارسال نمایید."
            ),
            message_type="REGISTRATION_RESULT",
        )

        # ثبت تصمیم مدیریتی در گروه مدیریتی (نه خصوصی مدیران)
        mgmt_group_id = self.get_management_group_id()
        if mgmt_group_id:
            self.announcements.enqueue_messages(
                recipients=[{"chat_id": mgmt_group_id, "user_id": None}],
                text=(
                    f"✅ تصمیم مدیریتی ثبت‌نام #{req_id}\n"
                    f"👤 متقاضی: {created_user['full_name']} ({created_user['bale_user_id']})\n"
                    f"🎖 نقش نهایی: {role_label(final_role)}\n"
                    f"🛡 تأییدکننده: {actor['full_name']}"
                ),
                message_type="REGISTRATION_DECISION_GROUP",
            )
        return created_user

    def reject_registration(self, actor: Dict[str, Any], req_id: int, reason: str) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "APPROVE_REGISTRATION", "REGISTRATION_REQUEST", req_id)
        clean_reason = sanitize_text(reason, max_length=300, field_label="علت رد درخواست")
        req = self.reg_repo.get_by_id(req_id)
        if not req:
            raise ValidationError("درخواست ثبت‌نام یافت نشد.")
        if req["status"] != "PENDING":
            raise ValidationError("این درخواست قبلاً تعیین تکلیف شده است.")

        if not self.reg_repo.transition_status_atomic(req_id, "REJECTED", actor["id"], rejection_reason=clean_reason):
            raise ValidationError("این درخواست هم‌زمان توسط مسئول دیگری بررسی شده است.")

        updated_req = self.reg_repo.get_by_id(req_id)
        self.audit.log(
            action="REJECT_REGISTRATION",
            entity_type="REGISTRATION_REQUEST",
            actor=actor,
            entity_id=req_id,
            after_data={"status": "REJECTED", "reason": clean_reason},
            description=f"رد درخواست #{req_id} توسط {actor['full_name']}",
        )

        self.announcements.enqueue_messages(
            recipients=[{"chat_id": req["bale_user_id"], "user_id": None}],
            text=f"❌ درخواست ثبت‌نام شما رد شد.\n📝 علت: {clean_reason}",
            message_type="REGISTRATION_RESULT",
        )

        mgmt_group_id = self.get_management_group_id()
        if mgmt_group_id:
            self.announcements.enqueue_messages(
                recipients=[{"chat_id": mgmt_group_id, "user_id": None}],
                text=(
                    f"❌ تصمیم مدیریتی ثبت‌نام #{req_id}: رد درخواست\n"
                    f"👤 متقاضی: {req['full_name']} ({req['bale_user_id']})\n"
                    f"📝 علت: {clean_reason}\n"
                    f"🛡 بررسی‌کننده: {actor['full_name']}"
                ),
                message_type="REGISTRATION_DECISION_GROUP",
            )
        return updated_req

    def cancel_own_request(self, bale_user_id: int) -> bool:
        pending = self.reg_repo.get_pending_by_bale_id(bale_user_id)
        if not pending:
            return False
        ok = self.reg_repo.cancel_by_user(pending["id"], bale_user_id)
        if ok:
            self.audit.log(
                action="CANCEL_REGISTRATION",
                entity_type="REGISTRATION_REQUEST",
                actor_bale_id=bale_user_id,
                entity_id=pending["id"],
                description="لغو درخواست ثبت‌نام توسط متقاضی",
            )
        return ok

    def create_user_direct(
        self,
        actor: Dict[str, Any],
        bale_user_id: int,
        full_name: str,
        role: str,
        phone: Optional[str] = None,
        student_code: Optional[str] = None,
        grade_level: Optional[str] = None,
        specialty: Optional[str] = None,
        web_password: Optional[str] = None,
    ) -> Dict[str, Any]:
        clean_role = validate_role(role, self_registration=False)
        self.permissions.ensure_can_manage_user(actor, target_user=None, target_new_role=clean_role)
        clean_name = validate_full_name(full_name)
        clean_phone = validate_phone(phone, required=False)
        clean_stu = validate_student_code(student_code, required=False)
        if self.users.get_by_bale_id(bale_user_id):
            raise ValidationError("کاربری با این شناسه بله قبلاً ثبت شده است.")
        if clean_stu and self.users.get_by_student_code(clean_stu):
            raise ValidationError("این کد دانش‌آموزی تکراری است.")
        pw_hash = hash_password(web_password) if web_password else None

        user = self.users.create(
            bale_user_id=int(bale_user_id),
            full_name=clean_name,
            role=clean_role,
            phone=clean_phone,
            student_code=clean_stu,
            grade_level=grade_level,
            specialty=specialty,
            web_password_hash=pw_hash,
            is_active=1,
        )
        self.audit.log(
            action="CREATE_USER",
            entity_type="USER",
            actor=actor,
            entity_id=user["id"],
            after_data=user,
            description=f"ایجاد مستقیم کاربر {clean_name} با نقش {clean_role}",
        )
        return user

    def change_user_role(self, actor: Dict[str, Any], target_user_id: int, new_role: str) -> Dict[str, Any]:
        clean_role = validate_role(new_role, self_registration=False)
        target = self.users.get_by_id(target_user_id)
        if not target:
            raise ValidationError("کاربر موردنظر یافت نشد.")
        self.permissions.ensure_can_manage_user(actor, target_user=target, target_new_role=clean_role)
        old_role = target["role"]
        updated = self.users.update_fields(target_user_id, role=clean_role)
        self.audit.log(
            action="CHANGE_USER_ROLE",
            entity_type="USER",
            actor=actor,
            entity_id=target_user_id,
            before_data={"role": old_role},
            after_data={"role": clean_role},
            description=f"تغییر نقش {target['full_name']} از {old_role} به {clean_role}",
        )
        return updated

    def update_user_info(
        self,
        actor: Dict[str, Any],
        target_user_id: int,
        full_name: Optional[str] = None,
        phone: Optional[str] = None,
        student_code: Optional[str] = None,
        grade_level: Optional[str] = None,
        specialty: Optional[str] = None,
    ) -> Dict[str, Any]:
        target = self.users.get_by_id(target_user_id)
        if not target:
            raise ValidationError("کاربر موردنظر یافت نشد.")
        if actor["id"] != target_user_id:
            self.permissions.ensure_can_manage_user(actor, target_user=target)
        updates: Dict[str, Any] = {}
        if full_name is not None:
            updates["full_name"] = validate_full_name(full_name)
        if phone is not None:
            updates["phone"] = validate_phone(phone, required=False)
        if student_code is not None:
            sc = validate_student_code(student_code, required=False)
            if sc and sc != target.get("student_code") and self.users.get_by_student_code(sc):
                raise ValidationError("کد دانش‌آموزی واردشده تکراری است.")
            updates["student_code"] = sc
        if grade_level is not None:
            updates["grade_level"] = validate_grade_level(grade_level) if grade_level.strip() else None
        if specialty is not None:
            updates["specialty"] = sanitize_text(specialty, 80, allow_empty=True) or None

        updated = self.users.update_fields(target_user_id, **updates)
        self.audit.log(
            action="UPDATE_USER",
            entity_type="USER",
            actor=actor,
            entity_id=target_user_id,
            before_data=target,
            after_data=updated,
            description=f"ویرایش اطلاعات کاربر {target['full_name']}",
        )
        return updated

    def set_user_active(self, actor: Dict[str, Any], target_user_id: int, is_active: bool) -> Dict[str, Any]:
        target = self.users.get_by_id(target_user_id)
        if not target:
            raise ValidationError("کاربر موردنظر یافت نشد.")
        self.permissions.ensure_can_manage_user(actor, target_user=target)
        if self.permissions.primary_admin_bale_id and target["bale_user_id"] == self.permissions.primary_admin_bale_id and not is_active:
            raise AccessDeniedError("غیرفعال‌سازی حساب ادمین اصلی سامانه مجاز نیست.")
        if actor["id"] == target_user_id and not is_active:
            raise ValidationError("امکان غیرفعال‌سازی حساب خودتان وجود ندارد.")
        updated = self.users.update_fields(target_user_id, is_active=1 if is_active else 0)
        self.audit.log(
            action="DEACTIVATE_USER" if not is_active else "ACTIVATE_USER",
            entity_type="USER",
            actor=actor,
            entity_id=target_user_id,
            before_data={"is_active": target["is_active"]},
            after_data={"is_active": updated["is_active"]},
            description=f"تغییر وضعیت فعال بودن حساب {target['full_name']}",
        )
        return updated

    def link_parent_student(self, actor: Dict[str, Any], parent_user_id: int, student_user_id: int) -> None:
        self.permissions.require_permission(actor, "MANAGE_USERS", "USER", parent_user_id)
        parent = self.users.get_by_id(parent_user_id)
        student = self.users.get_by_id(student_user_id)
        if not parent or parent["role"] != "PARENT":
            raise ValidationError("کاربر انتخاب‌شده دارای نقش ولی دانش‌آموز نیست.")
        if not student or student["role"] != "STUDENT":
            raise ValidationError("کاربر انتخاب‌شده دانش‌آموز نیست.")
        self.users.link_parent_student(parent_user_id, student_user_id)
        self.audit.log(
            action="LINK_PARENT_STUDENT",
            entity_type="PARENT_STUDENT",
            actor=actor,
            entity_id=f"{parent_user_id}:{student_user_id}",
            description=f"اتصال ولی ({parent['full_name']}) به دانش‌آموز ({student['full_name']})",
        )

    def grant_association_permissions(
        self,
        actor: Dict[str, Any],
        member_user_id: int,
        permissions_list: List[str],
    ) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "DELEGATE_ASSOCIATION_PERMISSIONS", "USER", member_user_id)
        target = self.users.get_by_id(member_user_id)
        if not target or target["role"] != "ASSOCIATION_MEMBER":
            raise ValidationError("اعطای مجوزهای انجمن فقط برای نقش عضو انجمن امکان‌پذیر است.")
        clean = [p.upper() for p in permissions_list if p.upper() in ASSOCIATION_DELEGATABLE_PERMISSIONS]
        updated = self.users.set_custom_permissions(member_user_id, clean)
        self.audit.log(
            action="DELEGATE_PERMISSIONS",
            entity_type="USER",
            actor=actor,
            entity_id=member_user_id,
            before_data={"custom_permissions": target.get("custom_permissions")},
            after_data={"custom_permissions": updated.get("custom_permissions")},
            description=f"تنظیم مجوزهای عضو انجمن {target['full_name']}",
        )
        return updated

    def set_web_password(self, actor: Dict[str, Any], target_user_id: int, new_password: str) -> None:
        target = self.users.get_by_id(target_user_id)
        if not target:
            raise ValidationError("کاربر یافت نشد.")
        if actor["id"] != target_user_id:
            self.permissions.ensure_can_manage_user(actor, target_user=target)
        pw_hash = hash_password(new_password)
        self.users.update_fields(target_user_id, web_password_hash=pw_hash, failed_web_logins=0, locked_until=None)
        self.audit.log(
            action="SET_WEB_PASSWORD",
            entity_type="USER",
            actor=actor,
            entity_id=target_user_id,
            description=f"تنظیم یا تغییر رمز عبور پنل وب برای {target['full_name']}",
        )

    def authenticate_web_user(
        self,
        bale_user_id: int,
        password: str,
        max_attempts: int = 5,
        lockout_minutes: int = 15,
        ip_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        user = self.users.get_by_bale_id(bale_user_id)
        now_str = now_iso()
        if not user or not user.get("is_active"):
            self.audit.log(
                action="WEB_LOGIN_FAILED",
                entity_type="AUTH",
                actor_bale_id=bale_user_id,
                result="FAILURE",
                ip_address=ip_address,
                description="تلاش ورود ناموفق به پنل وب (کاربر ناموجود یا غیرفعال)",
            )
            raise AccessDeniedError("شناسه کاربری یا رمز عبور نادرست است.")

        if user.get("locked_until") and user["locked_until"] > now_str:
            self.audit.log(
                action="WEB_LOGIN_LOCKED",
                entity_type="AUTH",
                actor=user,
                result="DENIED",
                ip_address=ip_address,
                description="تلاش ورود در زمان قفل موقت حساب",
            )
            raise AccessDeniedError("حساب شما به دلیل تلاش‌های ناموفق متوالی موقتاً قفل شده است. کمی بعد تلاش کنید.")

        if not verify_password(user.get("web_password_hash"), password):
            fails = int(user.get("failed_web_logins") or 0) + 1
            locked_until = None
            if fails >= max_attempts:
                locked_until = (now_tehran() + timedelta(minutes=lockout_minutes)).strftime("%Y-%m-%dT%H:%M:%S")
            self.users.update_fields(user["id"], failed_web_logins=fails, locked_until=locked_until)
            self.audit.log(
                action="WEB_LOGIN_FAILED",
                entity_type="AUTH",
                actor=user,
                result="FAILURE",
                ip_address=ip_address,
                description=f"رمز عبور نادرست در پنل وب (تلاش {fails})",
            )
            raise AccessDeniedError("شناسه کاربری یا رمز عبور نادرست است.")

        # بررسی مجوز ورود به پنل وب مدیریتی/آموزشی (ادمین، مدیر، معاون، دبیر، عضو انجمن مجاز)
        allowed_web_roles = {"ADMIN", "SCHOOL_MANAGER", "DEPUTY", "TEACHER", "ASSOCIATION_MEMBER"}
        if user["role"] not in allowed_web_roles:
            self.audit.log(
                action="WEB_LOGIN_ROLE_DENIED",
                entity_type="AUTH",
                actor=user,
                result="DENIED",
                ip_address=ip_address,
                description="نقش کاربری مجاز به ورود به پنل وب مدیریتی نیست.",
            )
            raise AccessDeniedError("نقش کاربری شما مجوز ورود به پنل وب مدیریتی را ندارد.")

        self.users.update_fields(user["id"], failed_web_logins=0, locked_until=None)
        refreshed = self.users.get_by_id(user["id"])
        self.audit.log(
            action="WEB_LOGIN_SUCCESS",
            entity_type="AUTH",
            actor=refreshed,
            result="SUCCESS",
            ip_address=ip_address,
            description="ورود موفق به پنل وب",
        )
        return refreshed

    def authenticate_student_portal(
        self,
        identifier: str,
        password: str,
        max_attempts: int = 5,
        lockout_minutes: int = 15,
        ip_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        """احراز هویت امن درگاه کارنامه آنلاین دانش‌آموز و ولی با کد دانش‌آموزی یا شناسه بله."""
        ident = (identifier or "").strip()
        user = self.users.get_by_student_code(ident) if ident else None
        if not user and ident.isdigit():
            user = self.users.get_by_bale_id(int(ident))

        now_str = now_iso()
        if not user or not user.get("is_active") or user["role"] not in {"STUDENT", "PARENT"}:
            self.audit.log(
                action="PORTAL_LOGIN_FAILED",
                entity_type="AUTH",
                result="FAILURE",
                ip_address=ip_address,
                description=f"ورود ناموفق به درگاه کارنامه آنلاین ({ident[:20]})",
            )
            raise AccessDeniedError("کد دانش‌آموزی/شناسه کاربری یا رمز کارنامه آنلاین نادرست است.")

        if user.get("locked_until") and user["locked_until"] > now_str:
            raise AccessDeniedError("حساب شما به دلیل تلاش‌های ناموفق متوالی موقتاً قفل شده است. چند دقیقه دیگر تلاش کنید.")

        if not verify_password(user.get("web_password_hash"), password):
            fails = int(user.get("failed_web_logins") or 0) + 1
            locked_until = None
            if fails >= max_attempts:
                locked_until = (now_tehran() + timedelta(minutes=lockout_minutes)).strftime("%Y-%m-%dT%H:%M:%S")
            self.users.update_fields(user["id"], failed_web_logins=fails, locked_until=locked_until)
            self.audit.log(
                action="PORTAL_LOGIN_FAILED",
                entity_type="AUTH",
                actor=user,
                result="FAILURE",
                ip_address=ip_address,
                description=f"رمز نادرست در درگاه کارنامه آنلاین (تلاش {fails})",
            )
            raise AccessDeniedError("کد دانش‌آموزی/شناسه کاربری یا رمز کارنامه آنلاین نادرست است.")

        self.users.update_fields(user["id"], failed_web_logins=0, locked_until=None)
        refreshed = self.users.get_by_id(user["id"])
        self.audit.log(
            action="PORTAL_LOGIN_SUCCESS",
            entity_type="AUTH",
            actor=refreshed,
            result="SUCCESS",
            ip_address=ip_address,
            description="ورود موفق به درگاه کارنامه آنلاین",
        )
        return refreshed

