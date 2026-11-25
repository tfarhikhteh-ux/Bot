from __future__ import annotations

from balethon import Client
from balethon.conditions import equals, private
from balethon.objects import CallbackQuery, InlineKeyboard, InlineKeyboardButton, Message

from keyboards.admin import BTN_USERS_MGMT
from keyboards.main import confirm_inline, wizard_cancel_keyboard
from keyboards.manager import registration_review_inline, users_mgmt_inline
from keyboards.student import BTN_PARENT_CHILDREN
from utils.formatters import role_label
from utils.helpers import safe_answer_callback
from utils.validators import ValidationError

from . import BotContext, cb_prefix

FLOW_USER = "USER_MGMT_FLOW"


def _user_actions_inline(user: dict) -> InlineKeyboard:
    toggle_lbl = "🚫 غیرفعال‌سازی حساب" if user["is_active"] else "✅ فعال‌سازی حساب"
    toggle_val = "0" if user["is_active"] else "1"
    return InlineKeyboard(
        [
            InlineKeyboardButton("✏️ ویرایش نام", callback_data=f"usr:edit_name:{user['id']}"),
            InlineKeyboardButton("🎖 تغییر نقش", callback_data=f"role:pick:{user['id']}"),
        ],
        [
            InlineKeyboardButton(toggle_lbl, callback_data=f"usr:ask_toggle:{user['id']}:{toggle_val}"),
        ],
    )


def register(client: Client, ctx: BotContext) -> None:
    @client.on_message(private & equals(BTN_USERS_MGMT))
    async def users_menu(*, client: Client, message: Message):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "MANAGE_USERS", "USER")
            await message.reply("👥 مدیریت کاربران و درخواست‌های ثبت‌نام:", reply_markup=users_mgmt_inline())

        await ctx.run_safe_message(client, message, _run)

    @client.on_message(private & equals(BTN_PARENT_CHILDREN))
    async def parent_children_info(*, client: Client, message: Message):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "VIEW_CHILDREN_INFO", "USER")
            children = ctx.users_repo.get_children_of_parent(actor["id"])
            if not children:
                await message.reply("هنوز فرزندی به حساب کاربری شما متصل نشده است. با مدیریت مدرسه هماهنگ کنید.")
                return
            lines = ["👨‍👩‍👧 اطلاعات فرزندان ثبت‌شده شما:", "━" * 22]
            for ch in children:
                cls = ctx.services.timetable.classes.get_student_active_class(ch["id"])
                lines.append(
                    f"👤 نام: {ch['full_name']}\n"
                    f"   ▫️ کد دانش‌آموزی: {ch.get('student_code') or '-'}\n"
                    f"   ▫️ پایه: {ch.get('grade_level') or '-'} | کلاس: {cls['name'] if cls else 'تخصیص‌نیافته'}"
                )
            await message.reply("\n".join(lines))

        await ctx.run_safe_message(client, message, _run)

    @client.on_callback_query(cb_prefix("usr:"))
    async def user_callbacks(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "MANAGE_USERS", "USER")
            parts = callback_query.data.split(":")
            action = parts[1]
            uid = int(callback_query.author.id)
            chat_id = callback_query.message.chat.id

            if action == "pending_regs":
                reqs = ctx.services.registration.reg_repo.list_requests(status="PENDING", limit=15)
                await safe_answer_callback(callback_query, f"{len(reqs)} درخواست در انتظار")
                if not reqs:
                    await client.send_message(chat_id, "✅ هیچ درخواست ثبت‌نام در انتظاری وجود ندارد.")
                    return
                for r in reqs:
                    await client.send_message(
                        chat_id,
                        ctx.services.registration.format_management_request_report(r),
                        reply_markup=registration_review_inline(r["id"]),
                    )
            elif action == "search":
                ctx.users_repo.set_session(uid, FLOW_USER, "SEARCH_QUERY", {})
                await safe_answer_callback(callback_query, "عبارت جست‌وجو را بفرستید.")
                await client.send_message(
                    chat_id,
                    "🔍 نام، کد دانش‌آموزی، شماره تلفن یا شناسه بله کاربر را وارد کنید:",
                    reply_markup=wizard_cancel_keyboard(),
                )
            elif action == "create":
                ctx.users_repo.set_session(uid, FLOW_USER, "CREATE_BALE_ID", {})
                await safe_answer_callback(callback_query, "ایجاد کاربر جدید")
                await client.send_message(
                    chat_id,
                    "➕ شناسه عددی بله کاربر جدید را وارد کنید:",
                    reply_markup=wizard_cancel_keyboard(),
                )
            elif action == "link_parent":
                ctx.users_repo.set_session(uid, FLOW_USER, "LINK_PARENT_BALE", {})
                await safe_answer_callback(callback_query, "اتصال ولی به دانش‌آموز")
                await client.send_message(
                    chat_id,
                    "🔗 شناسه بله «ولی دانش‌آموز» را وارد کنید:",
                    reply_markup=wizard_cancel_keyboard(),
                )
            elif action == "view":
                target = ctx.users_repo.get_by_id(int(parts[2]))
                if not target:
                    raise ValidationError("کاربر یافت نشد.")
                await safe_answer_callback(callback_query, "مشخصات کاربر")
                await client.send_message(
                    chat_id,
                    f"👤 مشخصات کاربر #{target['id']}\n"
                    f"▫️ نام: {target['full_name']}\n"
                    f"▫️ شناسه بله: {target['bale_user_id']}\n"
                    f"▫️ نقش: {role_label(target['role'])}\n"
                    f"▫️ تلفن: {target.get('phone') or '-'}\n"
                    f"▫️ کد دانش‌آموزی: {target.get('student_code') or '-'}\n"
                    f"▫️ وضعیت: {'فعال 🟢' if target['is_active'] else 'غیرفعال 🔴'}",
                    reply_markup=_user_actions_inline(target),
                )
            elif action == "edit_name":
                target_id = int(parts[2])
                ctx.users_repo.set_session(uid, FLOW_USER, "EDIT_NAME", {"target_user_id": target_id})
                await safe_answer_callback(callback_query, "نام جدید را بفرستید.")
                await client.send_message(chat_id, "✏️ نام و نام خانوادگی جدید را وارد کنید:", reply_markup=wizard_cancel_keyboard())
            elif action == "ask_toggle":
                target_id, flag = int(parts[2]), int(parts[3])
                target = ctx.users_repo.get_by_id(target_id)
                if not target:
                    raise ValidationError("کاربر یافت نشد.")
                verb = "فعال‌سازی" if flag else "غیرفعال‌سازی"
                await safe_answer_callback(callback_query, "نیاز به تأیید")
                await client.send_message(
                    chat_id,
                    f"⚠️ آیا از {verb} حساب کاربری «{target['full_name']}» اطمینان دارید؟",
                    reply_markup=confirm_inline(f"usr:do_toggle:{target_id}:{flag}"),
                )
            elif action == "do_toggle":
                target_id, flag = int(parts[2]), int(parts[3])
                updated = ctx.services.registration.set_user_active(actor, target_id, bool(flag))
                await safe_answer_callback(callback_query, "انجام شد.")
                await callback_query.message.edit_text(
                    f"✅ وضعیت حساب «{updated['full_name']}» به {'فعال' if updated['is_active'] else 'غیرفعال'} تغییر یافت."
                )

        await ctx.run_safe_callback(client, callback_query, _run, sensitive=True)

    ctx.register_session_flow(FLOW_USER, _handle_user_step)


async def _handle_user_step(ctx: BotContext, client: Client, message: Message, session: dict) -> None:
    uid = int(message.author.id)
    actor = ctx.services.permissions.require_permission(ctx.get_actor(uid), "MANAGE_USERS", "USER")
    step = session["step"]
    data = session["data"]
    raw = (message.text or "").strip()

    if step == "SEARCH_QUERY":
        results = ctx.users_repo.search(query=raw, limit=12)
        ctx.users_repo.clear_session(uid)
        if not results:
            await message.reply("نتیجه‌ای یافت نشد.", reply_markup=ctx.main_markup_for(actor))
            return
        kb = InlineKeyboard()
        for u in results:
            kb.add_row(
                InlineKeyboardButton(
                    f"{'🟢' if u['is_active'] else '🔴'} {u['full_name']} ({role_label(u['role'])})",
                    callback_data=f"usr:view:{u['id']}",
                )
            )
        await message.reply("🔍 نتایج جست‌وجو (برای مدیریت روی نام کاربر بزنید):", reply_markup=kb)
        return

    if step == "CREATE_BALE_ID":
        if not raw.isdigit():
            raise ValidationError("شناسه بله باید عددی مثبت باشد.")
        data["bale_user_id"] = int(raw)
        ctx.users_repo.set_session(uid, FLOW_USER, "CREATE_NAME", data)
        await message.reply("👤 نام و نام خانوادگی کاربر جدید را وارد کنید:")
        return

    if step == "CREATE_NAME":
        data["full_name"] = raw
        ctx.users_repo.set_session(uid, FLOW_USER, "CREATE_ROLE", data)
        roles_hint = "STUDENT, PARENT, TEACHER, DEPUTY, ASSOCIATION_MEMBER" + (
            ", SCHOOL_MANAGER, ADMIN" if actor["role"] == "ADMIN" else ""
        )
        await message.reply(f"🎖 نقش کاربر را وارد کنید ({roles_hint}):")
        return

    if step == "CREATE_ROLE":
        data["role"] = raw.strip().upper()
        ctx.users_repo.set_session(uid, FLOW_USER, "CREATE_CODE", data)
        await message.reply("🔢 در صورت دانش‌آموز بودن «کد دانش‌آموزی» را وارد کنید (در غیر این صورت «ندارد» بفرستید):")
        return

    if step == "CREATE_CODE":
        code = None if raw in {"ندارد", "-"} else raw
        created = ctx.services.registration.create_user_direct(
            actor=actor,
            bale_user_id=int(data["bale_user_id"]),
            full_name=data["full_name"],
            role=data["role"],
            student_code=code,
        )
        ctx.users_repo.clear_session(uid)
        await message.reply(
            f"✅ کاربر «{created['full_name']}» با نقش «{role_label(created['role'])}» ثبت شد.",
            reply_markup=ctx.main_markup_for(actor),
        )
        return

    if step == "EDIT_NAME":
        updated = ctx.services.registration.update_user_info(actor, int(data["target_user_id"]), full_name=raw)
        ctx.users_repo.clear_session(uid)
        await message.reply(f"✅ نام کاربر به «{updated['full_name']}» تغییر یافت.", reply_markup=ctx.main_markup_for(actor))
        return

    if step == "LINK_PARENT_BALE":
        if not raw.isdigit():
            raise ValidationError("شناسه بله ولی باید عددی باشد.")
        parent = ctx.users_repo.get_by_bale_id(int(raw))
        if not parent or parent["role"] != "PARENT":
            raise ValidationError("کاربر ولی با این شناسه بله یافت نشد.")
        data["parent_id"] = parent["id"]
        ctx.users_repo.set_session(uid, FLOW_USER, "LINK_STUDENT_CODE", data)
        await message.reply("🔢 کد دانش‌آموزی یا شناسه بله دانش‌آموز را وارد کنید:")
        return

    if step == "LINK_STUDENT_CODE":
        student = ctx.users_repo.get_by_student_code(raw)
        if not student and raw.isdigit():
            student = ctx.users_repo.get_by_bale_id(int(raw))
        if not student or student["role"] != "STUDENT":
            raise ValidationError("دانش‌آموزی با این مشخصات یافت نشد.")
        ctx.services.registration.link_parent_student(actor, int(data["parent_id"]), student["id"])
        ctx.users_repo.clear_session(uid)
        await message.reply(
            f"✅ حساب ولی با موفقیت به دانش‌آموز «{student['full_name']}» متصل شد.",
            reply_markup=ctx.main_markup_for(actor),
        )
