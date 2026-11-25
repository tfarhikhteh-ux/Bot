from __future__ import annotations

from typing import Any, Dict, Sequence, Set

from balethon.objects import InlineKeyboard, InlineKeyboardButton, ReplyKeyboard

from .admin import (
    BTN_ANNOUNCEMENTS_MGMT,
    BTN_ATTENDANCE_MGMT,
    BTN_CLASSES_MGMT,
    BTN_GRADES_MGMT,
    BTN_HOLIDAYS_MGMT,
    BTN_REPORTS_MGMT,
    BTN_ROLES_MGMT,
    BTN_SUBJECTS_MGMT,
    BTN_TIMETABLE_MGMT,
    BTN_USERS_MGMT,
)
from .main import BTN_HELP

BTN_MANAGER_PANEL = "🏛 پنل مدیریت مدرسه"
BTN_DISCIPLINARY = "⚖️ پرونده انضباطی دانش‌آموزان"
BTN_VIEW_CALENDAR = "🗓 مشاهده تقویم مدرسه"
BTN_VIEW_ANNOUNCEMENTS = "📢 مشاهده اطلاعیه‌ها"


def manager_main_keyboard() -> ReplyKeyboard:
    return ReplyKeyboard(
        [BTN_MANAGER_PANEL, BTN_USERS_MGMT],
        [BTN_ROLES_MGMT, BTN_CLASSES_MGMT],
        [BTN_SUBJECTS_MGMT, BTN_TIMETABLE_MGMT],
        [BTN_ATTENDANCE_MGMT, BTN_GRADES_MGMT],
        [BTN_ANNOUNCEMENTS_MGMT, BTN_HOLIDAYS_MGMT],
        [BTN_REPORTS_MGMT, BTN_HELP],
        resize=True,
    )


def deputy_main_keyboard() -> ReplyKeyboard:
    return ReplyKeyboard(
        [BTN_ATTENDANCE_MGMT, BTN_DISCIPLINARY],
        [BTN_ANNOUNCEMENTS_MGMT, BTN_VIEW_CALENDAR],
        [BTN_HELP],
        resize=True,
    )


def association_main_keyboard(perms: Set[str]) -> ReplyKeyboard:
    rows = []
    row1 = [BTN_VIEW_ANNOUNCEMENTS, BTN_VIEW_CALENDAR]
    rows.append(row1)
    extra = []
    if "MANAGE_ANNOUNCEMENTS" in perms:
        extra.append(BTN_ANNOUNCEMENTS_MGMT)
    if "VIEW_REPORTS_ALL" in perms:
        extra.append(BTN_REPORTS_MGMT)
    if extra:
        rows.append(extra)
    if "MANAGE_HOLIDAYS" in perms:
        rows.append([BTN_HOLIDAYS_MGMT])
    rows.append([BTN_HELP])
    return ReplyKeyboard(*rows, resize=True)


def users_mgmt_inline() -> InlineKeyboard:
    return InlineKeyboard(
        [
            InlineKeyboardButton("📥 درخواست‌های در انتظار", callback_data="usr:pending_regs"),
            InlineKeyboardButton("🔍 جست‌وجوی کاربر", callback_data="usr:search"),
        ],
        [
            InlineKeyboardButton("➕ ایجاد کاربر جدید", callback_data="usr:create"),
            InlineKeyboardButton("🔗 اتصال ولی به دانش‌آموز", callback_data="usr:link_parent"),
        ],
    )


def registration_review_inline(req_id: int) -> InlineKeyboard:
    return InlineKeyboard(
        [
            InlineKeyboardButton("✅ تأیید درخواست", callback_data=f"reg:approve:{req_id}"),
            InlineKeyboardButton("❌ رد با ذکر علت", callback_data=f"reg:reject:{req_id}"),
        ]
    )


def classes_mgmt_inline(classes: Sequence[Dict[str, Any]]) -> InlineKeyboard:
    kb = InlineKeyboard(
        [
            InlineKeyboardButton("➕ ایجاد کلاس جدید", callback_data="cls:create"),
            InlineKeyboardButton("🔄 انتقال دانش‌آموز", callback_data="cls:transfer"),
        ]
    )
    for c in list(classes)[:8]:
        status = "🟢" if c["is_active"] else "⚪"
        kb.add_row(
            InlineKeyboardButton(
                f"{status} {c['name']} (پایه {c['grade_level']} | {c['active_student_count']} نفر)",
                callback_data=f"cls:view:{c['id']}",
            )
        )
    return kb


def subjects_mgmt_inline(subjects: Sequence[Dict[str, Any]]) -> InlineKeyboard:
    kb = InlineKeyboard(
        [
            InlineKeyboardButton("➕ تعریف درس جدید", callback_data="sub:create"),
            InlineKeyboardButton("📌 تخصیص درس به کلاس", callback_data="sub:assign"),
        ]
    )
    for s in list(subjects)[:8]:
        kb.add_row(
            InlineKeyboardButton(
                f"📘 {s['name']} ({s['grade_level']} - ضریب {s['default_coefficient']:g})",
                callback_data=f"sub:view:{s['id']}",
            )
        )
    return kb


def announcements_mgmt_inline() -> InlineKeyboard:
    return InlineKeyboard(
        [
            InlineKeyboardButton("📝 ارسال اطلاعیه جدید", callback_data="ann:create"),
            InlineKeyboardButton("📜 تاریخچه و وضعیت ارسال", callback_data="ann:history"),
        ]
    )


def holidays_mgmt_inline() -> InlineKeyboard:
    return InlineKeyboard(
        [
            InlineKeyboardButton("➕ ثبت تعطیلی / رویداد", callback_data="hol:create"),
            InlineKeyboardButton("📅 فهرست تعطیلات و تقویم", callback_data="hol:list"),
        ]
    )


def reports_mgmt_inline() -> InlineKeyboard:
    return InlineKeyboard(
        [
            InlineKeyboardButton("📊 خلاصه آمار کل مدرسه", callback_data="rep:overview"),
            InlineKeyboardButton("🏆 معدل کلاس‌ها و دبیران", callback_data="rep:academic"),
        ],
        [
            InlineKeyboardButton("📥 خروجی CSV حضور و غیاب", callback_data="rep:csv:ATTENDANCE"),
            InlineKeyboardButton("📄 خروجی PDF حضور و غیاب", callback_data="rep:pdf:ATTENDANCE"),
        ],
        [
            InlineKeyboardButton("📥 خروجی CSV نمرات", callback_data="rep:csv:GRADES"),
            InlineKeyboardButton("📄 خروجی PDF نمرات", callback_data="rep:pdf:GRADES"),
        ],
        [
            InlineKeyboardButton("🕵️ گزارش تغییرات نقش/نمره/برنامه", callback_data="rep:changes"),
        ],
    )
