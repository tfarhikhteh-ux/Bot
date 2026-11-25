from .admin import admin_main_keyboard, admin_panel_inline, backup_menu_inline, role_selection_inline
from .main import (
    BTN_CANCEL,
    BTN_HELP,
    BTN_MAIN_MENU,
    build_main_menu,
    confirm_inline,
    registration_roles_inline,
    unregistered_keyboard,
    wizard_cancel_keyboard,
)
from .manager import association_main_keyboard, deputy_main_keyboard, manager_main_keyboard
from .student import parent_main_keyboard, student_main_keyboard
from .teacher import teacher_main_keyboard

__all__ = [
    "BTN_CANCEL",
    "BTN_HELP",
    "BTN_MAIN_MENU",
    "admin_main_keyboard",
    "admin_panel_inline",
    "association_main_keyboard",
    "backup_menu_inline",
    "build_main_menu",
    "confirm_inline",
    "deputy_main_keyboard",
    "manager_main_keyboard",
    "parent_main_keyboard",
    "registration_roles_inline",
    "role_selection_inline",
    "student_main_keyboard",
    "teacher_main_keyboard",
    "unregistered_keyboard",
    "wizard_cancel_keyboard",
]
