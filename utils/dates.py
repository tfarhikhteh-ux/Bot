from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jdatetime

PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
ENGLISH_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

PERSIAN_WEEKDAYS = {
    0: "شنبه",
    1: "یکشنبه",
    2: "دوشنبه",
    3: "سه‌شنبه",
    4: "چهارشنبه",
    5: "پنجشنبه",
    6: "جمعه",
}

WEEKDAY_BY_NAME = {v: k for k, v in PERSIAN_WEEKDAYS.items()}
WEEKDAY_BY_NAME.update(
    {
        "سه شنبه": 3,
        "پنج شنبه": 5,
        "یک شنبه": 1,
        "چهار شنبه": 4,
    }
)


def get_school_tz(tz_name: str = "Asia/Tehran"):
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=3, minutes=30))


def to_persian_digits(text: object) -> str:
    return str(text).translate(PERSIAN_DIGITS)


def to_english_digits(text: str) -> str:
    return (text or "").translate(ENGLISH_DIGITS)


def now_tehran(tz_name: str = "Asia/Tehran") -> datetime:
    return datetime.now(get_school_tz(tz_name))


def now_iso(tz_name: str = "Asia/Tehran") -> str:
    return now_tehran(tz_name).strftime("%Y-%m-%dT%H:%M:%S")


def today_date(tz_name: str = "Asia/Tehran") -> date:
    return now_tehran(tz_name).date()


def today_iso(tz_name: str = "Asia/Tehran") -> str:
    return today_date(tz_name).isoformat()


def tehran_weekday_index(target_date: Optional[date] = None, tz_name: str = "Asia/Tehran") -> int:
    """تبدیل روز هفته میلادی به اندیس هفته ایرانی (شنبه=0 ... جمعه=6)."""
    d = target_date or today_date(tz_name)
    # در پایتون دوشنبه=0 ... شنبه=5، یکشنبه=6
    return (d.weekday() + 2) % 7


def gregorian_to_jalali(target: date) -> jdatetime.date:
    return jdatetime.date.fromgregorian(date=target)


def format_jalali_date(iso_date_str: Optional[str], persian_digits: bool = True) -> str:
    if not iso_date_str:
        return "-"
    raw = to_english_digits(str(iso_date_str).strip())[:10]
    try:
        g_date = date.fromisoformat(raw)
        j_date = gregorian_to_jalali(g_date)
        formatted = f"{j_date.year:04d}/{j_date.month:02d}/{j_date.day:02d}"
        return to_persian_digits(formatted) if persian_digits else formatted
    except ValueError:
        return str(iso_date_str)


def format_jalali_datetime(iso_dt_str: Optional[str], persian_digits: bool = True) -> str:
    if not iso_dt_str:
        return "-"
    clean = to_english_digits(str(iso_dt_str).strip()).replace(" ", "T")
    try:
        dt = datetime.fromisoformat(clean)
        j_dt = jdatetime.datetime.fromgregorian(datetime=dt)
        formatted = f"{j_dt.year:04d}/{j_dt.month:02d}/{j_dt.day:02d} {j_dt.hour:02d}:{j_dt.minute:02d}"
        return to_persian_digits(formatted) if persian_digits else formatted
    except ValueError:
        return str(iso_dt_str)


def parse_date_input(raw_input: str) -> str:
    """
    تبدیل تاریخ ورودی کاربر (شمسی مانند 1405/07/01 یا میلادی مانند 2026-09-23)
    به رشته استاندارد ISO میلادی YYYY-MM-DD برای ذخیره‌سازی یکپارچه در دیتابیس.
    """
    cleaned = to_english_digits((raw_input or "").strip()).replace("-", "/")
    parts = [p for p in cleaned.split("/") if p]
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError("قالب تاریخ نامعتبر است. نمونه صحیح: ۱۴۰۵/۰۷/۱۵ یا 2026-10-07")

    year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
    if 1300 <= year <= 1500:
        try:
            j_d = jdatetime.date(year, month, day)
            return j_d.togregorian().isoformat()
        except ValueError as exc:
            raise ValueError("تاریخ شمسی واردشده معتبر نیست.") from exc
    if 1990 <= year <= 2100:
        try:
            return date(year, month, day).isoformat()
        except ValueError as exc:
            raise ValueError("تاریخ میلادی واردشده معتبر نیست.") from exc
    raise ValueError("سال تاریخ واردشده خارج از محدوده مجاز است.")


def parse_datetime_input(raw_input: str) -> str:
    """تبدیل تاریخ و زمان ورودی (شمسی یا میلادی YYYY/MM/DD HH:MM) به ISO YYYY-MM-DDTHH:MM:SS."""
    cleaned = to_english_digits((raw_input or "").strip())
    parts = cleaned.split()
    if len(parts) == 1:
        iso_d = parse_date_input(parts[0])
        return f"{iso_d}T08:00:00"
    if len(parts) != 2:
        raise ValueError("قالب زمان‌بندی نامعتبر است. نمونه صحیح: ۱۴۰۵/۰۷/۱۵ ۰۸:۳۰")
    iso_d = parse_date_input(parts[0])
    time_parts = parts[1].split(":")
    if len(time_parts) not in (2, 3) or not all(tp.isdigit() for tp in time_parts):
        raise ValueError("قالب ساعت نامعتبر است. نمونه صحیح: ۰۸:۳۰")
    hour, minute = int(time_parts[0]), int(time_parts[1])
    sec = int(time_parts[2]) if len(time_parts) == 3 else 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= sec <= 59):
        raise ValueError("مقدار ساعت یا دقیقه خارج از محدوده معتبر است.")
    return f"{iso_d}T{hour:02d}:{minute:02d}:{sec:02d}"


def compute_week_type(
    target_iso_date: Optional[str] = None,
    term_start_iso: Optional[str] = None,
    manual_override: Optional[str] = None,
) -> str:
    """محاسبه هفته اول (WEEK_1) یا هفته دوم (WEEK_2) بر اساس تنظیم دستی یا تاریخ شروع ترم."""
    if manual_override in {"WEEK_1", "WEEK_2"}:
        return manual_override
    target = date.fromisoformat(target_iso_date) if target_iso_date else today_date()
    if term_start_iso:
        try:
            start = date.fromisoformat(term_start_iso[:10])
            # هم‌تراز کردن با شنبه هفته شروع ترم
            start_sat = start - timedelta(days=tehran_weekday_index(start))
            target_sat = target - timedelta(days=tehran_weekday_index(target))
            weeks_elapsed = (target_sat - start_sat).days // 7
            return "WEEK_1" if weeks_elapsed % 2 == 0 else "WEEK_2"
        except ValueError:
            pass
    j_d = gregorian_to_jalali(target)
    week_num = (j_d.togregorian().timetuple().tm_yday // 7)
    return "WEEK_1" if week_num % 2 == 0 else "WEEK_2"
