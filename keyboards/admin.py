from __future__ import annotations

from typing import Any, Dict, Sequence

from balethon.objects import InlineKeyboard, InlineKeyboardButton, ReplyKeyboard

from .main import BTN_HELP

BTN_ADMIN_PANEL = "⚙️ پنل مدیریت کل"
BTN_USERS_MGMT = "👥 مدیریت کاربران و ثبت‌نام"
BTN_ROLES_MGMT = "🔐 نقش‌ها و مجوزها"
BTN_CLASSES_MGMT = "🏫 مدیریت کلاس‌ها"
BTN_SUBJECTS_MGMT = "📚 مدیریت درس‌ها"
BTN_TIMETABLE_MGMT = "📅 برنامه هفتگی"
BTN_ATTENDANCE_MGMT = "📋 حضور و غیاب"
BTN_GRADES_MGMT = "📊 مدیریت نمرات"
BTN_ANNOUNCEMENTS_MGMT = "📢 اطلاعیه‌ها"
BTN_HOLIDAYS_MGMT = "🗓 تعطیلات و تقویم"
BTN_REPORTS_MGMT = "📈 گزارش‌ها و آمار"
BTN_BACKUP_MGMT = "💾 پشتیبان‌گیری و بازیابی"


def admin_main_keyboard() -> ReplyKeyboard:
    return ReplyKeyboard(
        [BTN_ADMIN_PANEL, BTN_USERS_MGMT],
        [BTN_ROLES_MGMT, BTN_CLASSES_MGMT],
        [BTN_SUBJECTS_MGMT, BTN_TIMETABLE_MGMT],
        [BTN_ATTENDANCE_MGMT, BTN_GRADES_MGMT],
        [BTN_ANNOUNCEMENTS_MGMT, BTN_HOLIDAYS_MGMT],
        [BTN_REPORTS_MGMT, BTN_BACKUP_MGMT],
        [BTN_HELP],
        resize=True,
    )


def admin_panel_inline() -> InlineKeyboard:
    return InlineKeyboard(
        [
            InlineKeyboardButton("➕ افزودن مدیر مدرسه", callback_data="adm:add_manager"),
            InlineKeyboardButton("👑 افزودن ادمین جدید", callback_data="adm:add_admin"),
        ],
        [
            InlineKeyboardButton("🔧 تنظیمات اصلی و امنیتی", callback_data="adm:settings"),
            InlineKeyboardButton("👥 تنظیم گروه مدیریتی", callback_data="adm:set_mgmt_group"),
        ],
        [
            InlineKeyboardButton("🕵️ گزارش ممیزی امنیتی (Audit)", callback_data="adm:audit_recent"),
            InlineKeyboardButton("🔑 تنظیم رمز پنل وب", callback_data="adm:set_web_pass"),
        ],
    )


def backup_menu_inline(backups: Sequence[Dict[str, Any]]) -> InlineKeyboard:
    kb = InlineKeyboard(
        [InlineKeyboardButton("➕ تهیه نسخه پشتیبان فوری", callback_data="bkp:create")]
    )
    for b in list(backups)[:5]:
        kb.add_row(
            InlineKeyboardButton(f"♻️ بازیابی {b['filename'][:26]}", callback_data=f"bkp:ask_restore:{b['id']}")
        )
    return kb


def role_selection_inline(target_user_id: int, include_admin_roles: bool = False) -> InlineKeyboard:
    kb = InlineKeyboard()
    if include_admin_roles:
        kb.add_row(
            InlineKeyboardButton("👑 ادمین", callback_data=f"role:ask:{target_user_id}:ADMIN"),
            InlineKeyboardButton("🏛 مدیر مدرسه", callback_data=f"role:ask:{target_user_id}:SCHOOL_MANAGER"),
        )
    kb.add_row(
        InlineKeyboardButton("🛡 ناظم", callback_data=f"role:ask:{target_user_id}:DEPUTY"),
        InlineKeyboardButton("🤝 عضو انجمن", callback_data=f"role:ask:{target_user_id}:ASSOCIATION_MEMBER"),
    )
    kb.add_row(
        InlineKeyboardButton("👨‍🏫 دبیر", callback_data=f"role:ask:{target_user_id}:TEACHER"),
        InlineKeyboardButton("🎓 دانش‌آموز", callback_data=f"role:ask:{target_user_id}:STUDENT"),
    )
    kb.add_row(
        InlineKeyboardButton("👪 ولی دانش‌آموز", callback_data=f"role:ask:{target_user_id}:PARENT"),
    )
    return kb
