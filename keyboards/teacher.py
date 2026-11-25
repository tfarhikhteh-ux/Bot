from __future__ import annotations

from typing import Any, Dict, Sequence

from balethon.objects import InlineKeyboard, InlineKeyboardButton, ReplyKeyboard

from .main import BTN_HELP
from .manager import BTN_VIEW_ANNOUNCEMENTS, BTN_VIEW_CALENDAR

BTN_TEACHER_CLASSES = "🏫 کلاس‌ها و درس‌های من"
BTN_TEACHER_TIMETABLE = "📅 برنامه هفتگی من"
BTN_TEACHER_ATTENDANCE = "📋 ثبت حضور و غیاب کلاس"
BTN_TEACHER_GRADES = "📊 ثبت و مدیریت نمرات"
BTN_TEACHER_HOMEWORK = "📝 تکالیف کلاسی"


def teacher_main_keyboard() -> ReplyKeyboard:
    return ReplyKeyboard(
        [BTN_TEACHER_CLASSES, BTN_TEACHER_TIMETABLE],
        [BTN_TEACHER_ATTENDANCE, BTN_TEACHER_GRADES],
        [BTN_TEACHER_HOMEWORK, BTN_VIEW_ANNOUNCEMENTS],
        [BTN_VIEW_CALENDAR, BTN_HELP],
        resize=True,
    )


def teacher_classes_inline(classes: Sequence[Dict[str, Any]], prefix: str) -> InlineKeyboard:
    kb = InlineKeyboard()
    for c in classes:
        kb.add_row(
            InlineKeyboardButton(
                f"🏫 {c['name']} (پایه {c['grade_level']})",
                callback_data=f"{prefix}:{c['id']}",
            )
        )
    return kb


def attendance_status_inline(class_id: int, student_id: int, period_num: int) -> InlineKeyboard:
    base = f"att:set:{class_id}:{student_id}:{period_num}"
    return InlineKeyboard(
        [
            InlineKeyboardButton("✅ حاضر", callback_data=f"{base}:PRESENT"),
            InlineKeyboardButton("❌ غایب", callback_data=f"{base}:ABSENT"),
        ],
        [
            InlineKeyboardButton("⏰ تأخیر", callback_data=f"{base}:LATE"),
            InlineKeyboardButton("🚪 خروج زودهنگام", callback_data=f"{base}:EARLY_LEAVE"),
        ],
        [
            InlineKeyboardButton("📄 غیبت موجه", callback_data=f"{base}:EXCUSED_ABSENT"),
            InlineKeyboardButton("⛔ غیبت غیرموجه", callback_data=f"{base}:UNEXCUSED_ABSENT"),
        ],
    )
