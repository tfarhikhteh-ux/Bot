from __future__ import annotations

from typing import Any, Dict, Optional, Set

from balethon.objects import InlineKeyboard, InlineKeyboardButton, ReplyKeyboard

BTN_MAIN_MENU = "🏠 بازگشت به منوی اصلی"
BTN_CANCEL = "❌ لغو عملیات"
BTN_HELP = "📖 راهنما و دستورها"
BTN_START_REGISTER = "📝 شروع ثبت‌نام"
BTN_CHECK_REG_STATUS = "⏳ وضعیت درخواست ثبت‌نام"
BTN_CANCEL_REG = "🚫 لغو درخواست ثبت‌نام"


def wizard_cancel_keyboard() -> ReplyKeyboard:
    return ReplyKeyboard(
        [BTN_CANCEL, BTN_MAIN_MENU],
        resize=True,
    )


def unregistered_keyboard(has_pending: bool = False) -> ReplyKeyboard:
    if has_pending:
        return ReplyKeyboard(
            [BTN_CHECK_REG_STATUS, BTN_CANCEL_REG],
            [BTN_HELP],
            resize=True,
        )
    return ReplyKeyboard(
        [BTN_START_REGISTER],
        [BTN_HELP],
        resize=True,
    )


def registration_roles_inline() -> InlineKeyboard:
    return InlineKeyboard(
        [
            InlineKeyboardButton("🎓 دانش‌آموز", callback_data="reg_role:STUDENT"),
            InlineKeyboardButton("👪 ولی دانش‌آموز", callback_data="reg_role:PARENT"),
        ],
        [
            InlineKeyboardButton("👨‍🏫 دبیر", callback_data="reg_role:TEACHER"),
            InlineKeyboardButton("🛡 ناظم / معاون", callback_data="reg_role:DEPUTY"),
        ],
        [
            InlineKeyboardButton("🤝 عضو انجمن اولیا", callback_data="reg_role:ASSOCIATION_MEMBER"),
        ],
    )


def confirm_inline(confirm_cb: str, cancel_cb: str = "nav:cancel") -> InlineKeyboard:
    return InlineKeyboard(
        [
            InlineKeyboardButton("✅ تأیید نهایی", callback_data=confirm_cb),
            InlineKeyboardButton("❌ انصراف", callback_data=cancel_cb),
        ]
    )


def build_main_menu(user: Optional[Dict[str, Any]], effective_permissions: Optional[Set[str]] = None) -> ReplyKeyboard:
    if not user or not user.get("is_active"):
        return unregistered_keyboard(has_pending=False)

    role = (user.get("role") or "").upper()
    from .admin import admin_main_keyboard
    from .manager import association_main_keyboard, deputy_main_keyboard, manager_main_keyboard
    from .student import parent_main_keyboard, student_main_keyboard
    from .teacher import teacher_main_keyboard

    if role == "ADMIN":
        return admin_main_keyboard()
    if role == "SCHOOL_MANAGER":
        return manager_main_keyboard()
    if role == "DEPUTY":
        return deputy_main_keyboard()
    if role == "ASSOCIATION_MEMBER":
        return association_main_keyboard(effective_permissions or set())
    if role == "TEACHER":
        return teacher_main_keyboard()
    if role == "PARENT":
        return parent_main_keyboard()
    return student_main_keyboard()
