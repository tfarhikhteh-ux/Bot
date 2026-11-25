from __future__ import annotations

from typing import Dict, List, Set

from balethon import Client
from balethon.conditions import equals, private

from keyboards.main import BTN_HELP
from utils.formatters import role_label
from utils.helpers import send_chunked_message

from . import BotContext

# کاتالوگ واقعی دستورهای پیاده‌سازی‌شده به تفکیک مجوز و نقش
COMMANDS_CATALOG: List[Dict[str, object]] = [
    {
        "cmd": "/start",
        "purpose": "شروع کار و بازگشت به منوی اصلی",
        "desc": "بررسی وضعیت ثبت‌نام کاربر و نمایش منوی اختصاصی متناسب با نقش واقعی.",
        "access_label": "همه کاربران",
        "example": "/start",
        "roles": None,
        "perm": None,
    },
    {
        "cmd": "/دستورها",
        "purpose": "نمایش فهرست کامل دستورهای مجاز",
        "desc": "نمایش تمام دستورهایی که نقش فعلی شما اجازه اجرای آن‌ها را دارد همراه با راهنما.",
        "access_label": "همه کاربران",
        "example": "/دستورها",
        "roles": None,
        "perm": None,
    },
    {
        "cmd": "/لغو",
        "purpose": "لغو فرم چندمرحله‌ای جاری",
        "desc": "خروج ایمن از هر فرم در حال تکمیل و بازگشت به منوی اصلی بدون ذخیره تغییرات ناقص.",
        "access_label": "همه کاربران",
        "example": "/لغو",
        "roles": None,
        "perm": None,
    },
    {
        "cmd": "/مدیریت",
        "purpose": "ورود به پنل مدیریت مدرسه و سامانه",
        "desc": "باز کردن داشبورد مدیریتی متناسب با سطح ادمین کل یا مدیر مدرسه.",
        "access_label": "ادمین کل / مدیر مدرسه",
        "example": "/مدیریت",
        "roles": {"ADMIN", "SCHOOL_MANAGER"},
        "perm": None,
    },
    {
        "cmd": "/برنامه_ویرایش",
        "purpose": "ویرایش یا پاک‌کردن سریع خانه برنامه هفتگی",
        "desc": "ثبت یا تغییر درس یک زنگ مشخص با پشتیبانی هفته اول، هفته دوم و هر دو هفته (یا «حذف» برای پاک‌کردن).",
        "access_label": "ادمین کل / مدیر مدرسه",
        "example": "/برنامه_ویرایش دهم-الف | شنبه | 1 | هفته اول | ریاضی ۱",
        "roles": None,
        "perm": "MANAGE_TIMETABLE",
    },
    {
        "cmd": "/برنامه",
        "purpose": "مشاهده برنامه هفتگی",
        "desc": "نمایش جدول برنامه هفتگی کلاس دانش‌آموز، فرزند یا برنامه تدریس شخصی دبیر.",
        "access_label": "دانش‌آموز / ولی / دبیر / مدیر / ادمین",
        "example": "/برنامه",
        "roles": {"STUDENT", "PARENT", "TEACHER", "ADMIN", "SCHOOL_MANAGER"},
        "perm": None,
    },
    {
        "cmd": "/کارنامه",
        "purpose": "مشاهده کارنامه آنلاین، ریزنمرات و معدل وزنی",
        "desc": "نمایش لحظه‌ای نمرات مستمر، پایانی، مثبت/منفی‌های درسی، لینک کارنامه آنلاین وب و خروجی PDF و اکسل.",
        "access_label": "دانش‌آموز / ولی",
        "example": "/کارنامه",
        "roles": {"STUDENT", "PARENT"},
        "perm": None,
    },
    {
        "cmd": "مثبت درسی / منفی درسی",
        "purpose": "افزودن +۱ یا کسر -۱ از نمره مستمر (ریپلای در گروه یا فوروارد به شخصی ربات)",
        "desc": "یا در گروه روی پیام دانش‌آموز ریپلای کنید و دستور را بفرستید، یا پیام دانش‌آموز را به پی‌وی ربات فوروارد کرده و بعد دستور را ارسال نمایید (فقط روی مستمر اعمال می‌شود و پایانی تغییر نمی‌کند).",
        "access_label": "ادمین / مدیر / ناظم / دبیر / انجمن",
        "example": "مثبت درسی   (یا: منفی درسی | فیزیک)",
        "roles": {"ADMIN", "SCHOOL_MANAGER", "DEPUTY", "TEACHER", "ASSOCIATION_MEMBER"},
        "perm": None,
    },
    {
        "cmd": "قفل گیف / قفل باز گیف / قفل رسانه / پنل قفل",
        "purpose": "بستن و باز کردن ارسال گیف، استیکر، عکس، فیلم، ویس، فایل، لینک، فوروارد و چت گروه",
        "desc": "مدیریت کامل محدودیت‌های رسانه و پیام در گروه‌های بله (همراه با دستورات پین، حذف پین و حذف پیام با ریپلای).",
        "access_label": "ادمین / مدیر / ناظم / دبیر / انجمن",
        "example": "قفل گیف  |  قفل باز گیف  |  قفل رسانه  |  باز رسانه  |  پنل قفل",
        "roles": {"ADMIN", "SCHOOL_MANAGER", "DEPUTY", "TEACHER", "ASSOCIATION_MEMBER"},
        "perm": "MANAGE_GROUP_LOCKS",
    },
    {
        "cmd": "/حضور",
        "purpose": "مشاهده یا مدیریت وضعیت حضور و غیاب",
        "desc": "نمایش کارکرد حضور، غیبت و تأخیر برای دانش‌آموز/ولی یا ورود به بخش ثبت برای کادر آموزشی.",
        "access_label": "کاربران ثبت‌نام‌شده مجاز",
        "example": "/حضور",
        "roles": {"STUDENT", "PARENT", "TEACHER", "DEPUTY", "SCHOOL_MANAGER", "ADMIN"},
        "perm": None,
    },
    {
        "cmd": "/تقویم",
        "purpose": "مشاهده تقویم آموزشی و تعطیلات پیش‌رو",
        "desc": "نمایش تعطیلات رسمی، اختصاصی، اضطراری و رویدادهای تقویمی مدرسه.",
        "access_label": "همه کاربران فعال",
        "example": "/تقویم",
        "roles": {"STUDENT", "PARENT", "TEACHER", "DEPUTY", "ASSOCIATION_MEMBER", "SCHOOL_MANAGER", "ADMIN"},
        "perm": None,
    },
    {
        "cmd": "/پشتیبان",
        "purpose": "مدیریت پشتیبان‌گیری و بازیابی پایگاه داده",
        "desc": "تهیه فوری پشتیبان سازگار SQLite، بررسی صحت فایل و بازیابی کنترل‌شده با تأیید.",
        "access_label": "فقط ادمین کل (ADMIN)",
        "example": "/پشتیبان",
        "roles": {"ADMIN"},
        "perm": "BACKUP_CREATE",
    },
]


def build_help_for_user(actor: dict | None, effective_perms: Set[str]) -> str:
    role = actor["role"].upper() if actor and actor.get("is_active") else None
    header_role = role_label(role) if role else "کاربر ثبت‌نام‌نشده"

    lines = [
        f"📖 راهنمای جامع دستورهای مجاز شما ({header_role})",
        "━" * 26,
    ]

    idx = 0
    for item in COMMANDS_CATALOG:
        req_roles = item["roles"]
        req_perm = item["perm"]
        if req_roles is not None and role not in req_roles:
            continue
        if req_perm is not None and req_perm not in effective_perms:
            continue
        idx += 1
        lines.append(
            f"\n{idx}. دستور: {item['cmd']}\n"
            f"   ▪️ کاربرد: {item['purpose']}\n"
            f"   ▪️ توضیح عملکرد: {item['desc']}\n"
            f"   ▪️ سطح دسترسی موردنیاز: {item['access_label']}\n"
            f"   ▪️ نمونه استفاده: {item['example']}"
        )

    lines.append("\n💡 نکته: تمام عملیات‌ها از طریق دکمه‌های فارسی منوی پایین نیز در دسترس هستند.")
    return "\n".join(lines)


def register(client: Client, ctx: BotContext) -> None:
    async def _send_help(client: Client, message):
        async def _run(actor):
            perms = ctx.services.permissions.get_effective_permissions(actor) if actor else set()
            help_text = build_help_for_user(actor, perms)
            is_priv = bool(
                message.chat and str(getattr(message.chat.type, "value", message.chat.type)).lower().endswith("private")
            )
            await send_chunked_message(
                client,
                message.chat.id,
                help_text,
                reply_markup=ctx.main_markup_for(actor) if is_priv else None,
            )

        await ctx.run_safe_message(client, message, _run)

    @client.on_command(name="دستورها")
    async def commands_fa(*_args, client: Client, message):
        await _send_help(client, message)

    @client.on_command(name="help")
    async def commands_en(*_args, client: Client, message):
        await _send_help(client, message)

    @client.on_message(private & equals(BTN_HELP))
    async def help_button(*, client: Client, message):
        await _send_help(client, message)
