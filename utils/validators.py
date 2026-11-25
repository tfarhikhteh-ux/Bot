from __future__ import annotations

import re
from typing import Optional, Tuple

from .dates import WEEKDAY_BY_NAME, parse_date_input, parse_datetime_input, to_english_digits

PHONE_REGEX = re.compile(r"^(?:\+98|0098|0)?9\d{9}$")
TIME_REGEX = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
CODE_REGEX = re.compile(r"^[A-Za-z0-9_\-]{2,30}$")
SAFE_FILENAME_REGEX = re.compile(r"^[A-Za-z0-9_.\-]+$")

VALID_ROLES = {
    "ADMIN",
    "SCHOOL_MANAGER",
    "ASSOCIATION_MEMBER",
    "DEPUTY",
    "TEACHER",
    "STUDENT",
    "PARENT",
}

SELF_REGISTRABLE_ROLES = {
    "STUDENT",
    "PARENT",
    "TEACHER",
    "DEPUTY",
    "ASSOCIATION_MEMBER",
}

VALID_ATTENDANCE_STATUSES = {
    "PRESENT",
    "ABSENT",
    "LATE",
    "EARLY_LEAVE",
    "EXCUSED_ABSENT",
    "UNEXCUSED_ABSENT",
}

VALID_WEEK_TYPES = {"BOTH", "WEEK_1", "WEEK_2"}

WEEK_TYPE_ALIASES = {
    "هر دو": "BOTH",
    "هردو": "BOTH",
    "مشترک": "BOTH",
    "both": "BOTH",
    "0": "BOTH",
    "هفته اول": "WEEK_1",
    "اول": "WEEK_1",
    "1": "WEEK_1",
    "week_1": "WEEK_1",
    "week1": "WEEK_1",
    "هفته دوم": "WEEK_2",
    "دوم": "WEEK_2",
    "2": "WEEK_2",
    "week_2": "WEEK_2",
    "week2": "WEEK_2",
}

VALID_SUBJECT_TYPES = {"عمومی", "تخصصی", "کارگاهی", "فوق‌برنامه", "آزمایشگاه"}
VALID_HOLIDAY_TYPES = {"OFFICIAL", "SCHOOL", "EMERGENCY", "EVENT"}


class ValidationError(ValueError):
    """خطای اعتبارسنجی ورودی کاربر با پیام فارسی قابل نمایش."""


def sanitize_text(value: Optional[str], max_length: int = 500, allow_empty: bool = False, field_label: str = "مقدار") -> str:
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", (value or "")).strip()
    if not cleaned and not allow_empty:
        raise ValidationError(f"وارد کردن «{field_label}» الزامی است.")
    if len(cleaned) > max_length:
        raise ValidationError(f"طول «{field_label}» نباید بیشتر از {max_length} کاراکتر باشد.")
    return cleaned


def validate_full_name(raw_name: str) -> str:
    cleaned = sanitize_text(raw_name, max_length=80, field_label="نام و نام خانوادگی")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) < 3:
        raise ValidationError("نام و نام خانوادگی باید حداقل ۳ کاراکتر باشد.")
    return cleaned


def validate_phone(raw_phone: Optional[str], required: bool = False) -> Optional[str]:
    cleaned = to_english_digits((raw_phone or "").strip()).replace(" ", "").replace("-", "")
    if not cleaned or cleaned in {"ندارد", "-"}:
        if required:
            raise ValidationError("وارد کردن شماره تلفن همراه الزامی است.")
        return None
    if not PHONE_REGEX.match(cleaned):
        raise ValidationError("شماره تلفن همراه معتبر نیست. نمونه صحیح: ۰۹۱۲۳۴۵۶۷۸۹")
    return "0" + cleaned[-10:]


def validate_student_code(raw_code: Optional[str], required: bool = False) -> Optional[str]:
    cleaned = to_english_digits((raw_code or "").strip())
    if not cleaned or cleaned in {"ندارد", "-"}:
        if required:
            raise ValidationError("وارد کردن کد دانش‌آموزی الزامی است.")
        return None
    if not CODE_REGEX.match(cleaned):
        raise ValidationError("کد دانش‌آموزی باید بین ۲ تا ۳۰ کاراکتر (عدد یا حروف انگلیسی) باشد.")
    return cleaned


def validate_grade_level(raw_grade: str) -> str:
    cleaned = sanitize_text(raw_grade, max_length=30, field_label="پایه تحصیلی")
    return to_english_digits(cleaned)


def validate_academic_year(raw_year: str) -> str:
    cleaned = to_english_digits(sanitize_text(raw_year, max_length=20, field_label="سال تحصیلی"))
    if not re.match(r"^\d{4}(?:[-/]\d{2,4})?$", cleaned):
        raise ValidationError("قالب سال تحصیلی معتبر نیست. نمونه صحیح: 1405-1406")
    return cleaned.replace("/", "-")


def validate_role(role: str, self_registration: bool = False) -> str:
    norm = (role or "").strip().upper()
    allowed = SELF_REGISTRABLE_ROLES if self_registration else VALID_ROLES
    if norm not in allowed:
        raise ValidationError("نقش کاربری انتخاب‌شده مجاز نیست.")
    return norm


def validate_time_hhmm(raw_time: str, field_label: str = "ساعت") -> str:
    cleaned = to_english_digits((raw_time or "").strip())
    if len(cleaned) == 4 and ":" in cleaned:
        cleaned = "0" + cleaned
    if not TIME_REGEX.match(cleaned):
        raise ValidationError(f"قالب «{field_label}» نامعتبر است. نمونه صحیح: 08:00")
    return cleaned


def validate_time_range(start_time: str, end_time: str) -> Tuple[str, str]:
    s = validate_time_hhmm(start_time, "ساعت شروع")
    e = validate_time_hhmm(end_time, "ساعت پایان")
    if s >= e:
        raise ValidationError("ساعت پایان زنگ باید بعد از ساعت شروع آن باشد.")
    return s, e


def validate_weekday(raw_day: str | int) -> int:
    if isinstance(raw_day, int):
        if 0 <= raw_day <= 6:
            return raw_day
        raise ValidationError("روز هفته نامعتبر است.")
    cleaned = to_english_digits(str(raw_day).strip())
    if cleaned.isdigit():
        idx = int(cleaned)
        if 0 <= idx <= 6:
            return idx
    if cleaned in WEEKDAY_BY_NAME:
        return WEEKDAY_BY_NAME[cleaned]
    raise ValidationError("روز هفته معتبر نیست (نمونه: شنبه، یکشنبه، ... یا عدد 0 تا 6).")


def validate_week_type(raw_week: str) -> str:
    cleaned = to_english_digits((raw_week or "").strip()).lower()
    if cleaned.upper() in VALID_WEEK_TYPES:
        return cleaned.upper()
    if cleaned in WEEK_TYPE_ALIASES:
        return WEEK_TYPE_ALIASES[cleaned]
    raise ValidationError("نوع هفته نامعتبر است (مقادیر مجاز: هر دو، هفته اول، هفته دوم).")


def validate_period_number(raw_period: str | int) -> int:
    cleaned = to_english_digits(str(raw_period).strip())
    if not cleaned.isdigit():
        raise ValidationError("شماره زنگ باید عددی بین ۱ تا ۱۲ باشد.")
    num = int(cleaned)
    if not (1 <= num <= 12):
        raise ValidationError("شماره زنگ باید بین ۱ تا ۱۲ باشد.")
    return num


def validate_score(raw_score: str | float, min_score: float = 0.0, max_score: float = 20.0) -> float:
    cleaned = to_english_digits(str(raw_score).strip()).replace("٫", ".").replace("/", ".")
    try:
        val = round(float(cleaned), 2)
    except ValueError as exc:
        raise ValidationError("نمره واردشده باید یک عدد معتبر باشد.") from exc
    if val < min_score or val > max_score:
        raise ValidationError(f"نمره باید در بازه مجاز {min_score} تا {max_score} باشد.")
    return val


def validate_positive_float(raw_value: str | float, field_label: str = "ضریب", max_val: float = 20.0) -> float:
    cleaned = to_english_digits(str(raw_value).strip()).replace("٫", ".")
    try:
        val = round(float(cleaned), 2)
    except ValueError as exc:
        raise ValidationError(f"«{field_label}» باید یک عدد معتبر باشد.") from exc
    if val <= 0 or val > max_val:
        raise ValidationError(f"«{field_label}» باید بزرگ‌تر از صفر و حداکثر {max_val} باشد.")
    return val


def validate_positive_int(raw_value: str | int, field_label: str = "تعداد", minimum: int = 1, maximum: int = 500) -> int:
    cleaned = to_english_digits(str(raw_value).strip())
    if not cleaned.isdigit():
        raise ValidationError(f"«{field_label}» باید عدد صحیح مثبت باشد.")
    val = int(cleaned)
    if val < minimum or val > maximum:
        raise ValidationError(f"«{field_label}» باید بین {minimum} تا {maximum} باشد.")
    return val


def validate_date(raw_date: str) -> str:
    try:
        return parse_date_input(raw_date)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def validate_datetime(raw_dt: str) -> str:
    try:
        return parse_datetime_input(raw_dt)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def validate_backup_filename(filename: str) -> str:
    cleaned = (filename or "").strip()
    if not SAFE_FILENAME_REGEX.match(cleaned) or ".." in cleaned or not cleaned.endswith(".db"):
        raise ValidationError("نام فایل پشتیبان نامعتبر یا غیرامن است.")
    return cleaned
