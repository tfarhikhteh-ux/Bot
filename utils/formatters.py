from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle

from .dates import PERSIAN_WEEKDAYS, format_jalali_date, format_jalali_datetime, to_persian_digits

ROLE_LABELS = {
    "ADMIN": "ادمین کل سامانه",
    "SCHOOL_MANAGER": "مدیر مدرسه",
    "ASSOCIATION_MEMBER": "عضو انجمن اولیا و مربیان",
    "DEPUTY": "ناظم / معاون اجرایی",
    "TEACHER": "دبیر",
    "STUDENT": "دانش‌آموز",
    "PARENT": "ولی دانش‌آموز",
}

ATTENDANCE_LABELS = {
    "PRESENT": "✅ حاضر",
    "ABSENT": "❌ غایب",
    "LATE": "⏰ تأخیر",
    "EARLY_LEAVE": "🚪 خروج زودهنگام",
    "EXCUSED_ABSENT": "📄 غیبت موجه",
    "UNEXCUSED_ABSENT": "⛔ غیبت غیرموجه",
}

WEEK_TYPE_LABELS = {
    "BOTH": "هر دو هفته",
    "WEEK_1": "هفته اول",
    "WEEK_2": "هفته دوم",
}

HOLIDAY_TYPE_LABELS = {
    "OFFICIAL": "تعطیلی رسمی",
    "SCHOOL": "تعطیلی اختصاصی مدرسه",
    "EMERGENCY": "تعطیلی اضطراری",
    "EVENT": "رویداد تقویمی آموزشی",
}

ANNOUNCEMENT_TARGET_LABELS = {
    "ALL": "تمام کاربران فعال",
    "ROLE": "نقش مشخص",
    "CLASS": "کلاس مشخص",
    "TEACHERS": "تمام دبیران",
    "PARENTS": "تمام والدین",
    "MANAGEMENT_GROUP": "گروه مدیریتی",
}


def role_label(role: Optional[str]) -> str:
    return ROLE_LABELS.get((role or "").upper(), str(role or "-"))


def attendance_label(status: Optional[str]) -> str:
    return ATTENDANCE_LABELS.get((status or "").upper(), str(status or "-"))


def week_type_label(week_type: Optional[str]) -> str:
    return WEEK_TYPE_LABELS.get((week_type or "").upper(), str(week_type or "-"))


def holiday_type_label(h_type: Optional[str]) -> str:
    return HOLIDAY_TYPE_LABELS.get((h_type or "").upper(), str(h_type or "-"))


def shape_rtl(text: Any) -> str:
    """شکل‌دهی حروف فارسی و جهت‌دهی راست‌به‌چپ برای خروجی تصویری PNG و PDF."""
    raw = str(text if text is not None else "")
    if not raw:
        return ""
    reshaped = arabic_reshaper.reshape(raw)
    return get_display(reshaped)


def find_persian_font_path() -> Optional[str]:
    candidates = [
        os.environ.get("SCHOOL_BOT_FONT_PATH", ""),
        r"C:\Windows\Fonts\tahoma.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ]
    for path_str in candidates:
        if path_str and Path(path_str).is_file():
            return str(Path(path_str))
    return None


def format_timetable_text(
    title: str,
    entries: Sequence[Dict[str, Any]],
    periods: Sequence[Dict[str, Any]],
    active_days: Sequence[int] = (0, 1, 2, 3, 4),
    for_teacher: bool = False,
) -> str:
    lines = [f"📅 {title}", "━" * 24]
    by_day: Dict[int, List[Dict[str, Any]]] = {d: [] for d in active_days}
    period_map = {p["period_number"]: p for p in periods}

    for item in entries:
        day = item["day_of_week"]
        if day in by_day:
            by_day[day].append(item)

    for day in active_days:
        day_name = PERSIAN_WEEKDAYS.get(day, str(day))
        lines.append(f"\n🔹 {day_name}:")
        day_items = sorted(by_day.get(day, []), key=lambda x: (x["period_number"], x["week_type"]))
        if not day_items:
            lines.append("   ▫️ برنامه‌ای ثبت نشده است.")
            continue
        for item in day_items:
            p_num = item["period_number"]
            p_info = period_map.get(p_num)
            time_txt = f" ({p_info['start_time']} تا {p_info['end_time']})" if p_info else ""
            wk_badge = "" if item["week_type"] == "BOTH" else f" [{week_type_label(item['week_type'])}]"
            if for_teacher:
                detail = f"{item.get('subject_name', '-')} | کلاس {item.get('class_name', '-')}"
            else:
                teacher = item.get("teacher_name") or "بدون دبیر"
                detail = f"{item.get('subject_name', '-')} ({teacher})"
            lines.append(f"   • زنگ {to_persian_digits(p_num)}{to_persian_digits(time_txt)}: {detail}{wk_badge}")
    return "\n".join(lines)


def render_timetable_png(
    title: str,
    entries: Sequence[Dict[str, Any]],
    periods: Sequence[Dict[str, Any]],
    active_days: Sequence[int] = (0, 1, 2, 3, 4),
    for_teacher: bool = False,
) -> bytes:
    """تولید تصویر جدول خوانای برنامه هفتگی (راست‌به‌چپ) با کتابخانه Pillow."""
    period_list = sorted(periods, key=lambda p: p["period_number"]) or [
        {"period_number": i, "title": f"زنگ {i}", "start_time": "", "end_time": ""} for i in range(1, 5)
    ]
    cols = len(period_list) + 1
    rows = len(active_days) + 1
    cell_w, cell_h = 210, 86
    margin = 28
    header_h = 64
    img_w = cols * cell_w + margin * 2
    img_h = rows * cell_h + margin * 2 + header_h

    img = Image.new("RGB", (img_w, img_h), color=(248, 250, 252))
    draw = ImageDraw.Draw(img)

    font_path = find_persian_font_path()
    if font_path:
        title_font = ImageFont.truetype(font_path, 20)
        head_font = ImageFont.truetype(font_path, 14)
        body_font = ImageFont.truetype(font_path, 13)
    else:
        title_font = head_font = body_font = ImageFont.load_default()

    draw.text((img_w // 2, margin + 16), shape_rtl(to_persian_digits(title)), fill=(15, 23, 42), font=title_font, anchor="mm")

    cell_lookup: Dict[tuple, List[str]] = {}
    for item in entries:
        key = (item["day_of_week"], item["period_number"])
        wk = "" if item["week_type"] == "BOTH" else f" ({week_type_label(item['week_type'])})"
        if for_teacher:
            label = f"{item.get('subject_name', '')} - {item.get('class_name', '')}{wk}"
        else:
            t_name = item.get("teacher_name") or ""
            label = f"{item.get('subject_name', '')}{wk}\n{t_name}".strip()
        cell_lookup.setdefault(key, []).append(label)

    top_y = margin + header_h
    # ستون راست‌به‌چپ: ستون روزها در سمت راست تصویر
    for r_idx in range(rows):
        for c_idx in range(cols):
            # c_idx=0 سمت راست‌ترین ستون (نام روز)
            x1 = img_w - margin - (c_idx + 1) * cell_w
            y1 = top_y + r_idx * cell_h
            x2 = x1 + cell_w
            y2 = y1 + cell_h
            is_header = r_idx == 0 or c_idx == 0
            bg = (226, 232, 240) if is_header else (255, 255, 255)
            draw.rectangle([x1, y1, x2, y2], fill=bg, outline=(148, 163, 184), width=1)

            if r_idx == 0 and c_idx == 0:
                txt = shape_rtl("روز / زنگ")
                draw.text(((x1 + x2) // 2, (y1 + y2) // 2), txt, fill=(15, 23, 42), font=head_font, anchor="mm")
            elif r_idx == 0:
                p = period_list[c_idx - 1]
                t_range = f"{p['start_time']}-{p['end_time']}" if p.get("start_time") else ""
                lines = [shape_rtl(to_persian_digits(f"زنگ {p['period_number']}")), shape_rtl(to_persian_digits(t_range))]
                draw.text(((x1 + x2) // 2, y1 + 28), lines[0], fill=(15, 23, 42), font=head_font, anchor="mm")
                if t_range:
                    draw.text(((x1 + x2) // 2, y1 + 54), lines[1], fill=(71, 85, 105), font=body_font, anchor="mm")
            elif c_idx == 0:
                day = active_days[r_idx - 1]
                draw.text(
                    ((x1 + x2) // 2, (y1 + y2) // 2),
                    shape_rtl(PERSIAN_WEEKDAYS.get(day, str(day))),
                    fill=(15, 23, 42),
                    font=head_font,
                    anchor="mm",
                )
            else:
                day = active_days[r_idx - 1]
                p_num = period_list[c_idx - 1]["period_number"]
                items = cell_lookup.get((day, p_num), [])
                if not items:
                    draw.text(((x1 + x2) // 2, (y1 + y2) // 2), "-", fill=(148, 163, 184), font=body_font, anchor="mm")
                else:
                    combined = "\n".join(items)[:65]
                    sub_lines = [shape_rtl(to_persian_digits(line)) for line in combined.split("\n") if line]
                    start_offset = (y1 + y2) // 2 - (len(sub_lines) - 1) * 10
                    for idx_l, s_line in enumerate(sub_lines[:3]):
                        draw.text(((x1 + x2) // 2, start_offset + idx_l * 20), s_line, fill=(30, 41, 59), font=body_font, anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_table_pdf(title: str, headers: Sequence[str], rows: Sequence[Sequence[Any]], landscape_mode: bool = False) -> bytes:
    """تولید گزارش یا برنامه هفتگی در قالب PDF استاندارد با پشتیبانی فونت فارسی."""
    buf = io.BytesIO()
    page_size = landscape(A4) if landscape_mode else A4
    doc = SimpleDocTemplate(buf, pagesize=page_size, rightMargin=24, leftMargin=24, topMargin=28, bottomMargin=28)

    font_name = "Helvetica"
    font_path = find_persian_font_path()
    if font_path:
        font_name = "SchoolPersianFont"
        if font_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(font_name, font_path))

    title_style = ParagraphStyle(
        name="PersianTitle",
        fontName=font_name,
        fontSize=14,
        alignment=1,
        spaceAfter=14,
    )

    # چیدمان ستون‌ها از راست به چپ برای خوانایی فارسی
    rtl_headers = [shape_rtl(to_persian_digits(h)) for h in reversed(headers)]
    table_data = [rtl_headers]
    for row in rows:
        table_data.append([shape_rtl(to_persian_digits(cell)) for cell in reversed(row)])

    tbl = Table(table_data, repeatRows=1)
    tbl.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("FONTSIZE", (0, 1), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#94a3b8")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    story = [Paragraph(shape_rtl(to_persian_digits(title)), title_style), Spacer(1, 8), tbl]
    doc.build(story)
    return buf.getvalue()


def format_report_card_text(report_card: Dict[str, Any]) -> str:
    student = report_card["student"]
    lines = [
        f"🎓 کارنامه آموزشی: {student['full_name']}",
        f"🏫 کلاس: {report_card.get('class_name') or '-'} | دوره: {report_card.get('term') or 'همه دوره‌ها'}",
        "━" * 24,
    ]
    subjects = report_card.get("subjects", [])
    if not subjects:
        lines.append("هنوز نمره‌ای برای این دوره ثبت نشده است.")
        return "\n".join(lines)

    for subj in subjects:
        avg_txt = to_persian_digits(f"{subj['weighted_average_20']:.2f}")
        coef_txt = to_persian_digits(f"{subj['coefficient']:g}")
        lines.append(f"📘 {subj['subject_name']} (ضریب {coef_txt}): میانگین {avg_txt} از ۲۰")
        for g in subj["items"]:
            lines.append(
                f"   ▫️ {g['evaluation_name']}: {to_persian_digits(g['score'])} از {to_persian_digits(g['max_score'])}"
                f" ({format_jalali_date(g['created_at'])})"
            )

    gpa_txt = to_persian_digits(f"{report_card['gpa']:.2f}")
    lines.append("━" * 24)
    lines.append(f"⭐ معدل کل وزنی (از ۲۰): {gpa_txt}")
    return "\n".join(lines)
