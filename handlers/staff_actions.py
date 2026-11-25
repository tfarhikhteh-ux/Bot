from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from balethon import Client
from balethon.conditions import Condition
from balethon.objects import CallbackQuery, InlineKeyboard, InlineKeyboardButton, Message

from keyboards.main import wizard_cancel_keyboard
from services import AccessDeniedError
from services.grade_service import AmbiguousTeacherSubjectError
from utils.dates import format_jalali_date, to_persian_digits, today_iso
from utils.formatters import attendance_label, format_report_card_text, role_label
from utils.helpers import safe_answer_callback, send_chunked_message
from utils.validators import ValidationError

from . import BotContext, cb_prefix

FLOW_STAFF_TARGET = "STAFF_TARGET_FLOW"
STAFF_ROLES = {"ADMIN", "SCHOOL_MANAGER", "DEPUTY", "TEACHER", "ASSOCIATION_MEMBER"}

_POS_MOD_RE = re.compile(r"^(?:/?مثبت(?:[_\s]+درسی)?|\+[1۱](?:\s*درسی)?)(?:\s*(?:\||\s)\s*(.+))?$")
_NEG_MOD_RE = re.compile(r"^(?:/?منفی(?:[_\s]+درسی)?|-[1۱](?:\s*درسی)?)(?:\s*(?:\||\s)\s*(.+))?$")
_GRADE_CMD_RE = re.compile(r"^(?:/?نمره)\s+([0-9۰-۹]+(?:\.[0-9۰-۹]+)?)(?:\s*\|\s*(.+))?$")
_DISC_CMD_RE = re.compile(r"^(?:/?(انضباطی|تذکر|تشویق))(?:\s+(.+))?$")
_ROLE_CMD_RE = re.compile(r"^(?:/?تغییر[_\s]+نقش)\s+(.+)$")

_ATTENDANCE_ALIASES: Dict[str, str] = {
    "حاضر": "PRESENT",
    "غایب": "ABSENT",
    "تاخیر": "LATE",
    "تأخیر": "LATE",
    "موجه": "EXCUSED",
    "غیبت موجه": "EXCUSED",
    "خروج زودهنگام": "EARLY_LEAVE",
    "بیمار": "SICK",
}

_PERSIAN_ROLE_MAP: Dict[str, str] = {
    "ادمین": "ADMIN",
    "admin": "ADMIN",
    "مدیر": "SCHOOL_MANAGER",
    "مدیر مدرسه": "SCHOOL_MANAGER",
    "school_manager": "SCHOOL_MANAGER",
    "ناظم": "DEPUTY",
    "معاون": "DEPUTY",
    "deputy": "DEPUTY",
    "معلم": "TEACHER",
    "دبیر": "TEACHER",
    "teacher": "TEACHER",
    "انجمن": "ASSOCIATION_MEMBER",
    "عضو انجمن": "ASSOCIATION_MEMBER",
    "association_member": "ASSOCIATION_MEMBER",
    "دانش آموز": "STUDENT",
    "دانش‌آموز": "STUDENT",
    "student": "STUDENT",
    "ولی": "PARENT",
    "والد": "PARENT",
    "parent": "PARENT",
}


def _is_private_chat(msg: Optional[Message]) -> bool:
    if not msg or not msg.chat or msg.chat.type is None:
        return False
    val = getattr(msg.chat.type, "value", msg.chat.type)
    return str(val).lower().endswith("private")


def _is_forwarded_message(msg: Optional[Message]) -> bool:
    if not msg:
        return False
    return bool(
        getattr(msg, "forward_from", None)
        or getattr(msg, "forward_date", None)
        or getattr(msg, "forward_sender_name", None)
        or getattr(msg, "forward_origin", None)
    )


def _normalize_digits(val: str) -> str:
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return val.translate(trans)


def _resolve_user_from_forwarded(ctx: BotContext, msg: Message) -> Dict[str, Any]:
    """استخراج کاربر هدف (دانش‌آموز یا هر عضو دیگر) از پیام فورواردشده در بله."""
    fwd_user = getattr(msg, "forward_from", None)
    fwd_uid: Optional[int] = int(fwd_user.id) if (fwd_user and getattr(fwd_user, "id", None) is not None) else None

    if fwd_uid is None:
        origin = getattr(msg, "forward_origin", None)
        if isinstance(origin, dict):
            sender_user = origin.get("sender_user") or {}
            if sender_user.get("id") is not None:
                fwd_uid = int(sender_user["id"])
        elif origin is not None and getattr(origin, "sender_user", None) is not None:
            su = getattr(origin, "sender_user")
            if getattr(su, "id", None) is not None:
                fwd_uid = int(su.id)

    if fwd_uid is not None:
        target = ctx.users_repo.get_by_bale_id(fwd_uid)
        if not target:
            raise ValidationError("فرستنده اصلی این پیام هنوز در سامانه مدرسه ثبت‌نام نشده است.")
        return target

    sender_name = (getattr(msg, "forward_sender_name", None) or "").strip()
    if sender_name:
        matches = [
            u
            for u in ctx.users_repo.search_users(query=sender_name, active_only=False)
            if u["full_name"].strip() == sender_name
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValidationError(
                f"حریم خصوصی فوروارد کاربر بسته است و بیش از یک نفر با نام «{sender_name}» وجود دارد؛ لطفاً در گروه روی پیام او ریپلای کنید."
            )

    raise ValidationError(
        "شناسه فرستنده در پیام فورواردشده توسط تنظیمات حریم خصوصی بله مخفی شده یا در سامانه ثبت‌نام نکرده است."
    )


def _resolve_user_from_reply(ctx: BotContext, reply_msg: Message) -> Dict[str, Any]:
    if _is_forwarded_message(reply_msg):
        return _resolve_user_from_forwarded(ctx, reply_msg)
    if not reply_msg.author or getattr(reply_msg.author, "id", None) is None:
        raise ValidationError("فرستنده پیام ریپلای‌شده قابل تشخیص نیست.")
    target = ctx.users_repo.get_by_bale_id(int(reply_msg.author.id))
    if not target:
        raise ValidationError("کاربری که روی پیام او ریپلای کرده‌اید در سامانه ثبت‌نام نشده است.")
    return target


def parse_staff_target_command(
    text: Optional[str],
    *,
    allow_short_unreplied: bool = False,
    has_reply: bool = False,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """تشخیص دستورات هدفمند کارکنان هم در حالت ریپلای گروه و هم در حالت بعد از فوروارد در پی‌وی."""
    if not text:
        return None
    cleaned = " ".join(text.strip().split())
    low = cleaned.lower().replace("\u200c", " ")

    # ۱. مثبت درسی / منفی درسی (صرفاً ویژه معلم)
    for pattern, delta in ((_POS_MOD_RE, 1.0), (_NEG_MOD_RE, -1.0)):
        if not allow_short_unreplied and not cleaned.startswith("/") and "درسی" not in cleaned:
            continue
        m = pattern.match(cleaned)
        if m:
            tail = (m.group(1) or "").strip()
            if not tail:
                return ("modifier", {"delta": delta, "subject_hint": None, "note": None})
            if "|" in tail:
                p = [x.strip() for x in tail.split("|", 1)]
                return ("modifier", {"delta": delta, "subject_hint": p[0] or None, "note": p[1] or None})
            return ("modifier", {"delta": delta, "subject_hint": tail, "note": None})

    # دستورات چندکلمه‌ای صریح (حتی بدون ریپلای نیز تشخیص داده می‌شوند تا راهنمای دو روش نمایش داده شود)
    if low in {"کارنامه فرد", "کارنامه دانش آموز", "مشاهده کارنامه"}:
        return ("report_card", {})
    if low in {"غیرفعال کاربر", "مسدود سامانه", "غیرفعال کردن کاربر"}:
        return ("set_active", {"active": False})
    if low in {"فعال کاربر", "آزاد سامانه", "فعال کردن کاربر"}:
        return ("set_active", {"active": True})
    m_role = _ROLE_CMD_RE.match(cleaned)
    if m_role:
        return ("change_role", {"role_raw": m_role.group(1).strip()})

    # دستورات تک‌کلمه‌ای یا کوتاه فقط وقتی ریپلای شده باشد یا در پی‌وی بلافاصله بعد از فوروارد باشیم فعال می‌شوند
    if not (has_reply or allow_short_unreplied):
        return None

    if low in {"وضعیت", "پرونده", "پروفایل", "مشخصات"}:
        return ("status", {})
    if allow_short_unreplied and low == "کارنامه":
        return ("report_card", {})

    # حضور و غیاب سریع: مثلاً «غایب» یا «تاخیر | 2»
    att_parts = [p.strip() for p in cleaned.split("|", 1)]
    att_head = att_parts[0].lstrip("/")
    if att_head in _ATTENDANCE_ALIASES:
        period = 1
        if len(att_parts) > 1 and _normalize_digits(att_parts[1]).isdigit():
            period = int(_normalize_digits(att_parts[1]))
        return ("attendance", {"status": _ATTENDANCE_ALIASES[att_head], "period": period})

    # ثبت نمره سریع: «نمره 18.5» یا «نمره 19 | شیمی»
    m_grd = _GRADE_CMD_RE.match(cleaned)
    if m_grd:
        score_val = float(_normalize_digits(m_grd.group(1)))
        subj_hint = (m_grd.group(2) or "").strip() or None
        return ("grade", {"score": score_val, "subject_hint": subj_hint})

    # ثبت مورد انضباطی / تشویقی: «انضباطی بی‌نظمی در کلاس»
    m_disc = _DISC_CMD_RE.match(cleaned)
    if m_disc and m_disc.group(2):
        cat_word = m_disc.group(1)
        category = "تشویقی" if cat_word == "تشویق" else "انضباطی"
        return ("disciplinary", {"category": category, "description": m_disc.group(2).strip()})

    return None


def _format_modifier_result(res: dict) -> str:
    is_pos = res["delta"] > 0
    header = "➕ مثبت درسی (+۱ نمره مستمر) ثبت شد" if is_pos else "➖ منفی درسی (-۱ نمره مستمر) ثبت شد"
    old_txt = to_persian_digits(f"{res['old_score']:g}")
    new_txt = to_persian_digits(f"{res['new_score']:g}")
    max_txt = to_persian_digits(f"{res['max_score']:g}")
    pos_txt = to_persian_digits(res["positive_count"])
    neg_txt = to_persian_digits(res["negative_count"])
    return (
        f"{header}\n"
        f"👤 دانش‌آموز: {res['student']['full_name']}\n"
        f"🏫 کلاس: {res['class_name']} | 📘 درس: {res['subject_name']}\n"
        f"📌 نوع اعمال: {res['evaluation_name']} (فقط مستمر — بدون تغییر در پایانی)\n"
        f"⭐ نمره مستمر: {old_txt} ← {new_txt} از {max_txt}\n"
        f"📊 جمع امتیازات کلاسی این درس: +{pos_txt} / -{neg_txt}"
    )


def _build_forward_prompt_and_keyboard(
    ctx: BotContext,
    actor: Dict[str, Any],
    target: Dict[str, Any],
    fwd_msg_id: int,
) -> Tuple[str, InlineKeyboard]:
    """ساخت پیام راهنمای دستورات متنی + دکمه‌های شیشه‌ای سریع متناسب با نقش کارمند پس از فوروارد به پی‌وی."""
    actor_role = actor["role"].upper()
    actor_bid = actor["bale_user_id"]
    tid = target["id"]
    is_student = target["role"] == "STUDENT"

    kb = InlineKeyboard()
    cmd_hints = []

    if actor_role == "TEACHER":
        if is_student:
            kb.add_row(
                InlineKeyboardButton(
                    "➕ مثبت درسی (+۱ مستمر)",
                    callback_data=f"stf:mod:{actor_bid}:{tid}:pos:{fwd_msg_id}",
                    style="success",
                ),
                InlineKeyboardButton(
                    "➖ منفی درسی (-۱ مستمر)",
                    callback_data=f"stf:mod:{actor_bid}:{tid}:neg:{fwd_msg_id}",
                    style="danger",
                ),
            )
            kb.add_row(
                InlineKeyboardButton("✅ حاضر", callback_data=f"stf:att:{actor_bid}:{tid}:PRESENT", style="success"),
                InlineKeyboardButton("❌ غایب", callback_data=f"stf:att:{actor_bid}:{tid}:ABSENT", style="danger"),
                InlineKeyboardButton("⏰ تأخیر", callback_data=f"stf:att:{actor_bid}:{tid}:LATE"),
            )
            cmd_hints.extend(["مثبت درسی", "منفی درسی", "نمره ۱۸.۵", "حاضر / غایب / تاخیر", "وضعیت"])
        kb.add_row(
            InlineKeyboardButton("📊 مشاهده وضعیت", callback_data=f"stf:status:{actor_bid}:{tid}", style="primary")
        )
        if not cmd_hints:
            cmd_hints.append("وضعیت")

    elif actor_role == "DEPUTY":
        if is_student:
            kb.add_row(
                InlineKeyboardButton("❌ ثبت غیبت", callback_data=f"stf:att:{actor_bid}:{tid}:ABSENT", style="danger"),
                InlineKeyboardButton("⏰ ثبت تأخیر", callback_data=f"stf:att:{actor_bid}:{tid}:LATE"),
                InlineKeyboardButton("✅ ثبت حاضر", callback_data=f"stf:att:{actor_bid}:{tid}:PRESENT", style="success"),
            )
            kb.add_row(
                InlineKeyboardButton("⚠️ ثبت مورد انضباطی", callback_data=f"stf:disc_ask:{actor_bid}:{tid}", style="danger"),
                InlineKeyboardButton("📊 وضعیت و پرونده", callback_data=f"stf:status:{actor_bid}:{tid}", style="primary"),
            )
            cmd_hints.extend(["غایب / تاخیر / حاضر / موجه", "انضباطی <شرح>", "تشویق <شرح>", "وضعیت"])
        else:
            kb.add_row(
                InlineKeyboardButton("📊 مشاهده وضعیت", callback_data=f"stf:status:{actor_bid}:{tid}", style="primary")
            )
            cmd_hints.append("وضعیت")

    elif actor_role in {"ADMIN", "SCHOOL_MANAGER"}:
        row1 = [InlineKeyboardButton("👤 پرونده و وضعیت", callback_data=f"stf:status:{actor_bid}:{tid}", style="primary")]
        if is_student:
            row1.append(InlineKeyboardButton("🎓 مشاهده کارنامه", callback_data=f"stf:rc:{actor_bid}:{tid}", style="success"))
        kb.add_row(*row1)
        if is_student:
            kb.add_row(
                InlineKeyboardButton("❌ ثبت غیبت", callback_data=f"stf:att:{actor_bid}:{tid}:ABSENT", style="danger"),
                InlineKeyboardButton("⏰ ثبت تأخیر", callback_data=f"stf:att:{actor_bid}:{tid}:LATE"),
                InlineKeyboardButton("⚠️ مورد انضباطی", callback_data=f"stf:disc_ask:{actor_bid}:{tid}"),
            )
            cmd_hints.extend(["وضعیت", "کارنامه فرد", "غایب / تاخیر / حاضر", "انضباطی <شرح>"])
        else:
            cmd_hints.append("وضعیت")

        can_toggle = tid != actor["id"] and (
            actor_role == "ADMIN" or target["role"] not in {"ADMIN", "SCHOOL_MANAGER"}
        )
        if can_toggle:
            if target.get("is_active"):
                kb.add_row(
                    InlineKeyboardButton(
                        "🔒 غیرفعال‌سازی حساب کاربر",
                        callback_data=f"stf:act:{actor_bid}:{tid}:0",
                        style="danger",
                    )
                )
            else:
                kb.add_row(
                    InlineKeyboardButton(
                        "🔓 فعال‌سازی حساب کاربر",
                        callback_data=f"stf:act:{actor_bid}:{tid}:1",
                        style="success",
                    )
                )
            cmd_hints.extend(["غیرفعال کاربر / فعال کاربر", "تغییر نقش <نقش>"])

    elif actor_role == "ASSOCIATION_MEMBER":
        kb.add_row(
            InlineKeyboardButton("📊 مشاهده وضعیت مجاز", callback_data=f"stf:status:{actor_bid}:{tid}", style="primary")
        )
        cmd_hints.append("وضعیت")

    state_txt = "فعال ✅" if target.get("is_active") else "غیرفعال ⛔"
    prompt = (
        f"👤 فرد هدف شناسایی شد: «{target['full_name']}»\n"
        f"🎖 نقش در سامانه: {role_label(target['role'])} | وضعیت: {state_txt}\n\n"
        f"✍️ اکنون می‌توانید دستور موردنظر را تایپ کنید:\n"
        f"• {'  |  '.join(cmd_hints)}\n"
        f"یا از دکمه‌های سریع زیر متناسب با دسترسی خود استفاده نمایید:"
    )
    return prompt, kb


def _build_target_status_text(ctx: BotContext, actor: Dict[str, Any], target: Dict[str, Any]) -> str:
    """ساخت متن پرونده و وضعیت فرد با رعایت دقیق مرز دسترسی نقش فراخوانی‌کننده."""
    actor_role = actor["role"].upper()
    eff_perms = ctx.services.permissions.get_effective_permissions(actor)

    if actor_role == "TEACHER":
        if target["role"] != "STUDENT":
            raise AccessDeniedError("معلم فقط مجاز به مشاهده وضعیت دانش‌آموزان کلاس‌های خود است.")
        cls = ctx.services.timetable.classes.get_student_active_class(target["id"])
        if not cls:
            raise AccessDeniedError("این دانش‌آموز در هیچ کلاس فعالی ثبت نشده است.")
        ctx.services.permissions.ensure_teacher_class_subject(actor, cls["id"])
    elif actor_role == "ASSOCIATION_MEMBER":
        if "VIEW_REPORTS_ALL" not in eff_perms:
            raise AccessDeniedError("مشاهده پرونده وضعیت نیازمند تفویض مجوز «مشاهده گزارش‌ها» توسط مدیر مدرسه به عضو انجمن است.")
    elif actor_role not in {"ADMIN", "SCHOOL_MANAGER", "DEPUTY"}:
        raise AccessDeniedError("شما مجوز مشاهده پرونده وضعیت را ندارید.")

    lines = [
        f"📋 پرونده وضعیت کاربر: {target['full_name']}",
        f"🎖 نقش: {role_label(target['role'])} | وضعیت حساب: {'فعال ✅' if target.get('is_active') else 'غیرفعال ⛔'}",
        f"🆔 شناسه بله: {target['bale_user_id']}",
    ]
    if target.get("student_code"):
        lines.append(f"🎓 کد دانش‌آموزی: {target['student_code']}")

    if target["role"] == "STUDENT":
        cls = ctx.services.timetable.classes.get_student_active_class(target["id"])
        lines.append(f"🏫 کلاس فعال: {cls['name'] if cls else 'ثبت‌نشده'}")
        att_summary = ctx.services.attendance.attendance.summarize_status_counts(student_user_id=target["id"])
        lines.append(
            f"📅 خلاصه حضوروغیاب: حاضر {att_summary.get('PRESENT', 0)} | "
            f"غایب {att_summary.get('ABSENT', 0)} | تأخیر {att_summary.get('LATE', 0)} | موجه {att_summary.get('EXCUSED', 0)}"
        )
        if actor_role in {"ADMIN", "SCHOOL_MANAGER"} or (actor_role == "ASSOCIATION_MEMBER" and "VIEW_REPORTS_ALL" in eff_perms):
            rc = ctx.services.grades.get_student_report_card(None, target["id"], trusted_token_access=True)
            lines.append(f"⭐ معدل کل وزنی فعلی: {rc['gpa']:.2f} از ۲۰")
        elif actor_role == "TEACHER" and cls:
            rc = ctx.services.grades.get_student_report_card(None, target["id"], trusted_token_access=True)
            my_subj_ids = {
                cs["subject_id"]
                for cs in ctx.services.timetable.subjects.list_class_subjects(cls["id"])
                if cs.get("teacher_user_id") == actor["id"]
            }
            for sb in rc["subjects"]:
                if sb["subject_id"] in my_subj_ids:
                    lines.append(
                        f"📘 درس {sb['subject_name']}: مستمر {sb['continuous_score'] if sb['continuous_score'] is not None else '-'} "
                        f"(+{sb['positive_count']}/-{sb['negative_count']}) | میانگین {sb['weighted_average_20']:.2f}"
                    )

        if actor_role in {"ADMIN", "SCHOOL_MANAGER", "DEPUTY"}:
            notes = ctx.services.attendance.attendance.list_disciplinary_notes(student_user_id=target["id"], limit=3)
            if notes:
                lines.append("📝 آخرین موارد انضباطی/تشویقی:")
                for n in notes:
                    lines.append(f"  • [{format_jalali_date(n['note_date'])}] {n['category']}: {n['description']}")

    return "\n".join(lines)


async def _record_or_update_quick_attendance(
    ctx: BotContext,
    actor: Dict[str, Any],
    student: Dict[str, Any],
    status: str,
    period: int = 1,
) -> str:
    if student["role"] != "STUDENT" or not student.get("is_active"):
        raise ValidationError("حضور و غیاب فقط برای دانش‌آموز فعال قابل ثبت است.")
    cls = ctx.services.timetable.classes.get_student_active_class(student["id"])
    if not cls:
        raise ValidationError(f"دانش‌آموز «{student['full_name']}» هنوز در کلاس فعالی ثبت‌نام نشده است.")

    iso_today = today_iso()
    existing = ctx.services.attendance.attendance.list_records(
        class_id=cls["id"],
        student_user_id=student["id"],
        date_from=iso_today,
        date_to=iso_today,
        limit=20,
    )
    same_period = next((r for r in existing if int(r["period_number"]) == int(period)), None)
    if same_period:
        ctx.services.attendance.update_attendance(
            actor=actor,
            attendance_id=same_period["id"],
            new_status=status,
            reason="اصلاح با دستور مستقیم کارمند",
        )
    else:
        ctx.services.attendance.record_attendance(
            actor=actor,
            class_id=cls["id"],
            student_user_id=student["id"],
            status=status,
            att_date=iso_today,
            period_number=period,
        )
    return (
        f"✅ وضعیت حضور و غیاب ثبت شد:\n"
        f"👤 دانش‌آموز: {student['full_name']} | 🏫 کلاس: {cls['name']}\n"
        f"📌 وضعیت امروز (زنگ {to_persian_digits(period)}): {attendance_label(status)}"
    )


async def _execute_target_command(
    *,
    ctx: BotContext,
    client: Client,
    actor: Dict[str, Any],
    target: Dict[str, Any],
    cmd_type: str,
    params: Dict[str, Any],
    message: Message,
) -> None:
    is_priv = _is_private_chat(message)

    if cmd_type == "modifier":
        # طبق خواسته صریح: مثبت درسی و منفی درسی فقط و فقط مخصوص معلم همان درس است
        if actor["role"].upper() != "TEACHER":
            raise AccessDeniedError("دستور «مثبت درسی» و «منفی درسی» منحصراً مخصوص معلمِ همان درس است.")
        try:
            res = ctx.services.grades.apply_continuous_modifier(
                actor=actor,
                student_user_id=target["id"],
                delta=float(params["delta"]),
                subject_hint=params.get("subject_hint"),
                note=params.get("note"),
                source_chat_id=int(message.chat.id) if message.chat else None,
                source_message_id=int(message.id) if message.id else None,
            )
            await message.reply(_format_modifier_result(res))
            await ctx.flush_notifications(client)
        except AmbiguousTeacherSubjectError as amb:
            sign_code = "pos" if amb.delta > 0 else "neg"
            kb = InlineKeyboard()
            for cs in amb.subjects:
                btn_label = f"{'➕ مثبت' if amb.delta > 0 else '➖ منفی'} درسی: {cs['subject_name']}"
                kb.add_row(
                    InlineKeyboardButton(
                        btn_label,
                        callback_data=f"cmod:{actor['bale_user_id']}:{amb.student['id']}:{cs['subject_id']}:{sign_code}:{message.id or 0}",
                    )
                )
            await message.reply(
                f"👨‍🏫 شما در کلاس «{amb.cls['name']}» بیش از یک درس تدریس می‌کنید؛ درس موردنظر برای اعمال روی نمره مستمر «{amb.student['full_name']}» را انتخاب کنید:",
                reply_markup=kb,
            )
        return

    if cmd_type == "attendance":
        txt_res = await _record_or_update_quick_attendance(
            ctx, actor, target, status=params["status"], period=int(params.get("period", 1))
        )
        await message.reply(txt_res)
        await ctx.flush_notifications(client)
        return

    if cmd_type == "grade":
        if target["role"] != "STUDENT":
            raise ValidationError("ثبت نمره فقط برای دانش‌آموز مجاز است.")
        cls = ctx.services.timetable.classes.get_student_active_class(target["id"])
        if not cls:
            raise ValidationError("دانش‌آموز در کلاس فعالی ثبت‌نام نشده است.")
        all_cs = ctx.services.timetable.subjects.list_class_subjects(cls["id"])
        if actor["role"].upper() == "TEACHER":
            cands = [cs for cs in all_cs if cs.get("teacher_user_id") == actor["id"]]
        elif actor["role"].upper() in {"ADMIN", "SCHOOL_MANAGER"}:
            cands = list(all_cs)
        else:
            raise AccessDeniedError("شما مجوز ثبت نمره ندارید.")

        hint = params.get("subject_hint")
        if hint:
            cands = [cs for cs in cands if cs["subject_name"] == hint or hint in cs["subject_name"]]
        if len(cands) != 1:
            raise ValidationError("لطفاً نام درس را هم مشخص کنید؛ نمونه: نمره 18.5 | نام درس")
        term = ctx.services.notifications.settings.get("current_term", "ترم اول")
        cont_ev = ctx.services.grades.grades.get_or_create_continuous_eval_type(term)
        g_row = ctx.services.grades.record_grade(
            actor, target["id"], cls["id"], cands[0]["subject_id"], cont_ev["id"], params["score"]
        )
        await message.reply(
            f"✅ نمره {g_row['score']} برای «{target['full_name']}» در درس «{cands[0]['subject_name']}» ({cont_ev['name']}) ثبت شد."
        )
        await ctx.flush_notifications(client)
        return

    if cmd_type == "disciplinary":
        item = ctx.services.attendance.record_disciplinary_note(
            actor=actor,
            student_user_id=target["id"],
            category=params["category"],
            description=params["description"],
        )
        await message.reply(
            f"✅ مورد «{item['category']}» برای «{target['full_name']}» ثبت شد:\n📝 {item['description']}"
        )
        return

    if cmd_type == "status":
        report_txt = _build_target_status_text(ctx, actor, target)
        if is_priv:
            await send_chunked_message(client, message.chat.id, report_txt)
        else:
            # حفظ محرمانگی اطلاعات دانش‌آموز در گروه: ارسال به پی‌وی کارمند
            await send_chunked_message(client, actor["bale_user_id"], report_txt)
            await message.reply("📨 پرونده وضعیت فرد به گفتگوی خصوصی شما ارسال شد.")
        return

    if cmd_type == "report_card":
        eff_perms = ctx.services.permissions.get_effective_permissions(actor)
        if actor["role"] == "ASSOCIATION_MEMBER" and "VIEW_REPORTS_ALL" in eff_perms:
            rc = ctx.services.grades.get_student_report_card(None, target["id"], trusted_token_access=True)
        else:
            rc = ctx.services.grades.get_student_report_card(actor, target["id"])
        rc_txt = format_report_card_text(rc)
        if is_priv:
            await send_chunked_message(client, message.chat.id, rc_txt)
        else:
            await send_chunked_message(client, actor["bale_user_id"], rc_txt)
            await message.reply("📨 کارنامه دانش‌آموز به گفتگوی خصوصی شما ارسال شد.")
        return

    if cmd_type == "set_active":
        updated = ctx.services.registration.set_user_active(actor, target["id"], bool(params["active"]))
        st_word = "فعال ✅" if updated["is_active"] else "غیرفعال ⛔"
        await message.reply(f"✅ وضعیت حساب «{updated['full_name']}» به {st_word} تغییر یافت.")
        return

    if cmd_type == "change_role":
        raw_r = params["role_raw"].strip().lower().replace("\u200c", " ")
        role_code = _PERSIAN_ROLE_MAP.get(raw_r) or raw_r.upper()
        updated = ctx.services.registration.change_user_role(actor, target["id"], role_code)
        await message.reply(
            f"✅ نقش کاربری «{updated['full_name']}» به «{role_label(updated['role'])}» تغییر یافت."
        )


def register(client: Client, ctx: BotContext) -> None:
    class _PrivateForwardCondition(Condition):
        def __init__(self) -> None:
            super().__init__(can_process=Message)

        async def __call__(self, _client, event: Message) -> bool:
            return _is_private_chat(event) and _is_forwarded_message(event)

    class _DirectTargetCommandCondition(Condition):
        def __init__(self) -> None:
            super().__init__(can_process=Message)

        async def __call__(self, _client, event: Message) -> bool:
            if _is_forwarded_message(event) or not event.text:
                return False
            has_rep = event.reply_to_message is not None
            return parse_staff_target_command(event.text, allow_short_unreplied=False, has_reply=has_rep) is not None

    @client.on_message(_PrivateForwardCondition())
    async def private_forwarded_person_handler(*, client: Client, message: Message):
        async def _run(actor):
            checked = ctx.services.permissions.require_active_user(actor)
            uid = int(message.author.id)
            if checked["role"].upper() not in STAFF_ROLES:
                raise AccessDeniedError("فوروارد پیام کاربران برای اجرای دستورات مدیریتی/آموزشی ویژه کارکنان مدرسه است.")

            sess = ctx.users_repo.get_session(uid)
            if sess and sess.get("flow") != FLOW_STAFF_TARGET:
                await message.reply(
                    "⚠️ شما یک فرم باز در حال تکمیل دارید. ابتدا آن را کامل کنید یا «❌ لغو عملیات» را بزنید و سپس پیام فرد را فوروارد کنید."
                )
                return

            target = _resolve_user_from_forwarded(ctx, message)
            ctx.users_repo.set_session(
                uid,
                FLOW_STAFF_TARGET,
                "AWAIT_COMMAND",
                {"target_user_id": target["id"], "src_msg_id": int(message.id or 0)},
            )
            prompt, kb = _build_forward_prompt_and_keyboard(ctx, checked, target, int(message.id or 0))
            await message.reply(prompt, reply_markup=kb)

        await ctx.run_safe_message(client, message, _run)

    @client.on_message(_DirectTargetCommandCondition())
    async def staff_target_command_handler(*, client: Client, message: Message):
        async def _run(actor):
            has_rep = message.reply_to_message is not None
            parsed = parse_staff_target_command(message.text, allow_short_unreplied=False, has_reply=has_rep)
            if not parsed:
                return
            cmd_type, params = parsed
            checked = ctx.services.permissions.require_active_user(actor)
            if checked["role"].upper() not in STAFF_ROLES:
                raise AccessDeniedError("اجرای این دستور فقط ویژه کارکنان مجاز مدرسه است.")
            if cmd_type == "modifier" and checked["role"].upper() != "TEACHER":
                raise AccessDeniedError("دستور «مثبت درسی» و «منفی درسی» منحصراً مخصوص معلمِ همان درس است.")

            uid = int(message.author.id)
            target: Optional[Dict[str, Any]] = None

            if message.reply_to_message:
                target = _resolve_user_from_reply(ctx, message.reply_to_message)
                if _is_private_chat(message):
                    sess = ctx.users_repo.get_session(uid)
                    if sess and sess.get("flow") == FLOW_STAFF_TARGET:
                        ctx.users_repo.clear_session(uid)
            elif _is_private_chat(message):
                sess = ctx.users_repo.get_session(uid)
                if sess and sess.get("flow") == FLOW_STAFF_TARGET and sess.get("step") == "AWAIT_COMMAND":
                    target = ctx.users_repo.get_by_id(int(sess["data"]["target_user_id"]))
                    ctx.users_repo.clear_session(uid)

            if not target:
                await message.reply(
                    "⚠️ برای اجرای این دستور روی یک فرد، یکی از دو روش زیر را انجام دهید:\n"
                    "۱) در گروه روی پیام فرد موردنظر ریپلای (Reply) کنید و دستور را بفرستید.\n"
                    "۲) پیام آن فرد را به شخصی (پی‌وی) ربات فوروارد کنید و سپس دستور را بفرستید."
                )
                return

            await _execute_target_command(
                ctx=ctx,
                client=client,
                actor=checked,
                target=target,
                cmd_type=cmd_type,
                params=params,
                message=message,
            )

        await ctx.run_safe_message(client, message, _run)

    @client.on_callback_query(cb_prefix("stf:"))
    async def staff_quick_buttons_callback(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            checked = ctx.services.permissions.require_active_user(actor)
            parts = (callback_query.data or "").split(":")
            action = parts[1]
            expected_bale_id = int(parts[2])
            target_id = int(parts[3])

            if int(callback_query.author.id) != expected_bale_id:
                await safe_answer_callback(callback_query, "⛔ این دکمه فقط مخصوص کارمند صادرکننده است.", show_alert=True)
                return

            target = ctx.users_repo.get_by_id(target_id)
            if not target:
                raise ValidationError("کاربر هدف یافت نشد.")

            if action == "disc_ask":
                ctx.services.permissions.require_permission(checked, "RECORD_DISCIPLINARY")
                ctx.users_repo.set_session(
                    expected_bale_id,
                    FLOW_STAFF_TARGET,
                    "AWAIT_DISC_TEXT",
                    {"target_user_id": target_id},
                )
                await safe_answer_callback(callback_query, "شرح مورد انضباطی را بنویسید.")
                await client.send_message(
                    callback_query.message.chat.id,
                    f"📝 شرح مورد انضباطی برای «{target['full_name']}» را ارسال کنید:",
                    reply_markup=wizard_cancel_keyboard(),
                )
                return

            ctx.users_repo.clear_session(expected_bale_id)

            if action == "mod":
                if checked["role"].upper() != "TEACHER":
                    raise AccessDeniedError("دستور «مثبت درسی» و «منفی درسی» منحصراً مخصوص معلمِ همان درس است.")
                delta = 1.0 if parts[4] == "pos" else -1.0
                src_msg_id = int(parts[5]) if len(parts) > 5 and int(parts[5]) > 0 else None
                try:
                    res = ctx.services.grades.apply_continuous_modifier(
                        actor=checked,
                        student_user_id=target_id,
                        delta=delta,
                        source_chat_id=int(callback_query.message.chat.id) if src_msg_id else None,
                        source_message_id=src_msg_id,
                    )
                    await safe_answer_callback(callback_query, "در نمره مستمر ثبت شد.")
                    await callback_query.message.edit_text(_format_modifier_result(res))
                    await ctx.flush_notifications(client)
                except AmbiguousTeacherSubjectError as amb:
                    sign_code = "pos" if amb.delta > 0 else "neg"
                    kb = InlineKeyboard()
                    for cs in amb.subjects:
                        btn_label = f"{'➕ مثبت' if amb.delta > 0 else '➖ منفی'} درسی: {cs['subject_name']}"
                        kb.add_row(
                            InlineKeyboardButton(
                                btn_label,
                                callback_data=f"cmod:{checked['bale_user_id']}:{amb.student['id']}:{cs['subject_id']}:{sign_code}:{src_msg_id or 0}",
                            )
                        )
                    await safe_answer_callback(callback_query, "درس را انتخاب کنید.")
                    await callback_query.message.edit_text(
                        f"👨‍🏫 درس موردنظر برای اعمال روی نمره مستمر «{amb.student['full_name']}» را انتخاب کنید:",
                        reply_markup=kb,
                    )
            elif action == "att":
                status_code = parts[4]
                txt_res = await _record_or_update_quick_attendance(ctx, checked, target, status=status_code, period=1)
                await safe_answer_callback(callback_query, "حضور و غیاب ثبت شد.")
                await callback_query.message.edit_text(txt_res)
                await ctx.flush_notifications(client)
            elif action == "status":
                txt_status = _build_target_status_text(ctx, checked, target)
                await safe_answer_callback(callback_query, "پرونده آماده شد.")
                await send_chunked_message(client, callback_query.message.chat.id, txt_status)
            elif action == "rc":
                rc = ctx.services.grades.get_student_report_card(checked, target_id)
                await safe_answer_callback(callback_query, "کارنامه آماده شد.")
                await send_chunked_message(client, callback_query.message.chat.id, format_report_card_text(rc))
            elif action == "act":
                active_bool = parts[4] == "1"
                updated = ctx.services.registration.set_user_active(checked, target_id, active_bool)
                st_word = "فعال ✅" if updated["is_active"] else "غیرفعال ⛔"
                await safe_answer_callback(callback_query, "وضعیت حساب تغییر کرد.")
                await callback_query.message.edit_text(f"✅ وضعیت حساب «{updated['full_name']}» به {st_word} تغییر یافت.")

        await ctx.run_safe_callback(client, callback_query, _run, sensitive=True)

    @client.on_callback_query(cb_prefix("cmod:"))
    async def continuous_modifier_subject_callback(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            parts = (callback_query.data or "").split(":")
            expected_bale_id = int(parts[1])
            student_id = int(parts[2])
            subject_id = int(parts[3])
            delta = 1.0 if parts[4] == "pos" else -1.0
            src_msg_id = int(parts[5]) if len(parts) > 5 and int(parts[5]) > 0 else None

            if int(callback_query.author.id) != expected_bale_id:
                await safe_answer_callback(callback_query, "⛔ این دکمه فقط مخصوص دبیر صادرکننده دستور است.", show_alert=True)
                return

            ctx.users_repo.clear_session(expected_bale_id)
            res = ctx.services.grades.apply_continuous_modifier(
                actor=actor,
                student_user_id=student_id,
                delta=delta,
                subject_id=subject_id,
                source_chat_id=int(callback_query.message.chat.id) if src_msg_id else None,
                source_message_id=src_msg_id,
            )
            await safe_answer_callback(callback_query, "در نمره مستمر ثبت شد.")
            await callback_query.message.edit_text(_format_modifier_result(res))
            await ctx.flush_notifications(client)

        await ctx.run_safe_callback(client, callback_query, _run, sensitive=True)

    ctx.register_session_flow(FLOW_STAFF_TARGET, handle_staff_target_session)


async def handle_staff_target_session(ctx: BotContext, client: Client, message: Message, session: dict) -> None:
    actor = ctx.services.permissions.require_active_user(ctx.get_actor(int(message.author.id)))
    uid = int(message.author.id)
    step = session["step"]
    data = session["data"]
    raw = (message.text or "").strip()

    target = ctx.users_repo.get_by_id(int(data["target_user_id"]))
    if not target:
        ctx.users_repo.clear_session(uid)
        raise ValidationError("کاربر هدف یافت نشد.")

    if step == "AWAIT_DISC_TEXT":
        item = ctx.services.attendance.record_disciplinary_note(
            actor=actor,
            student_user_id=target["id"],
            category="انضباطی",
            description=raw,
        )
        ctx.users_repo.clear_session(uid)
        await message.reply(
            f"✅ مورد انضباطی برای «{target['full_name']}» ثبت شد:\n📝 {item['description']}",
            reply_markup=ctx.main_markup_for(actor),
        )
        return

    if step == "AWAIT_COMMAND":
        parsed = parse_staff_target_command(raw, allow_short_unreplied=True, has_reply=False)
        if not parsed:
            raise ValidationError(
                "دستور شناخته نشد. یکی از دستورات مجاز نقش خود را بفرستید (مثلاً: وضعیت، غایب، تاخیر، انضباطی <متن>، یا برای معلم: مثبت درسی / منفی درسی) یا «❌ لغو عملیات» را بزنید."
            )
        cmd_type, params = parsed
        ctx.users_repo.clear_session(uid)
        await _execute_target_command(
            ctx=ctx,
            client=client,
            actor=actor,
            target=target,
            cmd_type=cmd_type,
            params=params,
            message=message,
        )
