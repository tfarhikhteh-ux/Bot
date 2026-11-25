from __future__ import annotations

from typing import Any, Dict, Sequence

from balethon.objects import InlineKeyboard, InlineKeyboardButton, ReplyKeyboard

from .main import BTN_HELP
from .manager import BTN_VIEW_ANNOUNCEMENTS, BTN_VIEW_CALENDAR

BTN_STUDENT_TIMETABLE = "📅 برنامه هفتگی کلاس من"
BTN_STUDENT_GRADES = "🎓 کارنامه و نمرات من"
BTN_STUDENT_ATTENDANCE = "📋 وضعیت حضور و غیاب من"
BTN_STUDENT_HOMEWORK = "📚 تکالیف کلاس من"

BTN_PARENT_CHILDREN = "👨‍👩‍👧 فرزندان من"
BTN_PARENT_GRADES = "🎓 کارنامه و نمرات فرزند"
BTN_PARENT_ATTENDANCE = "📋 حضور و غیاب فرزند"
BTN_PARENT_TIMETABLE = "📅 برنامه هفتگی فرزند"


def student_main_keyboard() -> ReplyKeyboard:
    return ReplyKeyboard(
        [BTN_STUDENT_TIMETABLE, BTN_STUDENT_GRADES],
        [BTN_STUDENT_ATTENDANCE, BTN_STUDENT_HOMEWORK],
        [BTN_VIEW_ANNOUNCEMENTS, BTN_VIEW_CALENDAR],
        [BTN_HELP],
        resize=True,
    )


def parent_main_keyboard() -> ReplyKeyboard:
    return ReplyKeyboard(
        [BTN_PARENT_CHILDREN, BTN_PARENT_GRADES],
        [BTN_PARENT_ATTENDANCE, BTN_PARENT_TIMETABLE],
        [BTN_VIEW_ANNOUNCEMENTS, BTN_VIEW_CALENDAR],
        [BTN_HELP],
        resize=True,
    )


def children_selector_inline(children: Sequence[Dict[str, Any]], action_prefix: str) -> InlineKeyboard:
    kb = InlineKeyboard()
    for ch in children:
        kb.add_row(
            InlineKeyboardButton(
                f"👤 {ch['full_name']} (کد: {ch.get('student_code') or ch['id']})",
                callback_data=f"{action_prefix}:{ch['id']}",
            )
        )
    return kb


def timetable_view_options_inline(class_id: int) -> InlineKeyboard:
    return InlineKeyboard(
        [
            InlineKeyboardButton("📅 هفته اول", callback_data=f"tt:cls:{class_id}:WEEK_1"),
            InlineKeyboardButton("📅 هفته دوم", callback_data=f"tt:cls:{class_id}:WEEK_2"),
        ],
        [
            InlineKeyboardButton("📆 برنامه امروز", callback_data=f"tt:day:{class_id}"),
            InlineKeyboardButton("📋 کل برنامه", callback_data=f"tt:cls:{class_id}:ALL"),
        ],
        [
            InlineKeyboardButton("🖼 تصویر (PNG)", callback_data=f"tt:png:{class_id}"),
            InlineKeyboardButton("📄 فایل (PDF)", callback_data=f"tt:pdf:{class_id}"),
            InlineKeyboardButton("📗 اکسل (XLSX)", callback_data=f"tt:xlsx:{class_id}"),
        ],
    )


def report_card_export_inline(student_id: int) -> InlineKeyboard:
    return InlineKeyboard(
        [
            InlineKeyboardButton("🌐 لینک کارنامه آنلاین (وب)", callback_data=f"grd:web_link:{student_id}"),
            InlineKeyboardButton("🔄 بروزرسانی لحظه‌ای", callback_data=f"grd:refresh:{student_id}"),
        ],
        [
            InlineKeyboardButton("📄 دانلود کارنامه (PDF)", callback_data=f"grd:rc_pdf:{student_id}"),
            InlineKeyboardButton("📗 دانلود کارنامه اکسل (XLSX)", callback_data=f"grd:rc_xlsx:{student_id}"),
        ],
        [
            InlineKeyboardButton("🔑 تنظیم رمز درگاه کارنامه آنلاین", callback_data="grd:set_portal_pw"),
        ],
    )
