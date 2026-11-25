from __future__ import annotations

from balethon import Client
from balethon.conditions import equals, private
from balethon.objects import CallbackQuery, Message

from keyboards.main import (
    BTN_CANCEL_REG,
    BTN_CHECK_REG_STATUS,
    BTN_START_REGISTER,
    confirm_inline,
    registration_roles_inline,
    unregistered_keyboard,
    wizard_cancel_keyboard,
)
from keyboards.manager import registration_review_inline
from utils.formatters import role_label
from utils.helpers import safe_answer_callback
from utils.validators import validate_full_name, validate_grade_level, validate_phone, validate_student_code

from . import BotContext, cb_prefix

FLOW_REG = "REGISTRATION_FLOW"
FLOW_REG_REJECT = "REGISTRATION_REJECT_FLOW"


def register(client: Client, ctx: BotContext) -> None:
    @client.on_message(private & equals(BTN_START_REGISTER))
    async def begin_registration(*, client: Client, message: Message):
        async def _run(actor):
            uid = int(message.author.id)
            if actor and actor.get("is_active"):
                await message.reply("شما قبلاً ثبت‌نام شده‌اید.", reply_markup=ctx.main_markup_for(actor))
                return
            pending = ctx.services.registration.reg_repo.get_pending_by_bale_id(uid)
            if pending:
                await message.reply(
                    f"⏳ شما یک درخواست در انتظار بررسی دارید (شماره #{pending['id']}).",
                    reply_markup=unregistered_keyboard(has_pending=True),
                )
                return
            await message.reply(
                "📝 فرم ثبت‌نام در سامانه مدرسه\nلطفاً نقش موردنظر خود را انتخاب نمایید:",
                reply_markup=registration_roles_inline(),
            )

        await ctx.run_safe_message(client, message, _run)

    @client.on_message(private & equals(BTN_CHECK_REG_STATUS))
    async def check_reg_status(*, client: Client, message: Message):
        async def _run(actor):
            uid = int(message.author.id)
            pending = ctx.services.registration.reg_repo.get_pending_by_bale_id(uid)
            if not pending:
                await message.reply("درخواست فعالی در انتظار بررسی یافت نشد.", reply_markup=unregistered_keyboard(False))
                return
            await message.reply(
                f"⏳ وضعیت درخواست #{pending['id']}:\n"
                f"👤 نام: {pending['full_name']}\n"
                f"🎖 نقش درخواستی: {role_label(pending['requested_role'])}\n"
                f"📌 وضعیت: در انتظار تأیید مدیریت",
                reply_markup=unregistered_keyboard(True),
            )

        await ctx.run_safe_message(client, message, _run)

    @client.on_message(private & equals(BTN_CANCEL_REG))
    async def ask_cancel_reg(*, client: Client, message: Message):
        async def _run(actor):
            uid = int(message.author.id)
            pending = ctx.services.registration.reg_repo.get_pending_by_bale_id(uid)
            if not pending:
                await message.reply("درخواست فعالی برای لغو وجود ندارد.", reply_markup=unregistered_keyboard(False))
                return
            await message.reply(
                f"آیا از لغو درخواست ثبت‌نام شماره #{pending['id']} اطمینان دارید؟",
                reply_markup=confirm_inline("reg_self:confirm_cancel"),
            )

        await ctx.run_safe_message(client, message, _run)

    @client.on_callback_query(cb_prefix("reg_self:confirm_cancel"))
    async def confirm_cancel_reg(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            uid = int(callback_query.author.id)
            ok = ctx.services.registration.cancel_own_request(uid)
            await safe_answer_callback(callback_query, "درخواست لغو شد." if ok else "درخواستی یافت نشد.")
            await client.send_message(
                callback_query.message.chat.id,
                "✅ درخواست ثبت‌نام شما لغو شد." if ok else "درخواست فعالی وجود نداشت.",
                reply_markup=unregistered_keyboard(False),
            )

        await ctx.run_safe_callback(client, callback_query, _run, sensitive=True)

    @client.on_callback_query(cb_prefix("reg_role:"))
    async def pick_reg_role(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            uid = int(callback_query.author.id)
            if actor and actor.get("is_active"):
                await safe_answer_callback(callback_query, "شما قبلاً عضو سامانه هستید.", show_alert=True)
                return
            role = callback_query.data.split(":", 1)[1]
            ctx.users_repo.set_session(uid, FLOW_REG, "FULL_NAME", {"requested_role": role})
            await safe_answer_callback(callback_query, f"نقش {role_label(role)} انتخاب شد.")
            await client.send_message(
                callback_query.message.chat.id,
                f"🎖 نقش انتخابی: {role_label(role)}\n\nلطفاً «نام و نام خانوادگی» کامل خود را به فارسی وارد کنید:",
                reply_markup=wizard_cancel_keyboard(),
            )

        await ctx.run_safe_callback(client, callback_query, _run)

    @client.on_callback_query(cb_prefix("reg:approve:", "reg:reject:"))
    async def review_reg_callback(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "APPROVE_REGISTRATION", "REGISTRATION_REQUEST")
            parts = callback_query.data.split(":")
            action, req_id = parts[1], int(parts[2])
            if action == "approve":
                created = ctx.services.registration.approve_registration(actor, req_id)
                await safe_answer_callback(callback_query, "درخواست تأیید شد.")
                if callback_query.message:
                    await callback_query.message.edit_text(
                        f"✅ درخواست #{req_id} برای «{created['full_name']}» با نقش «{role_label(created['role'])}» "
                        f"توسط «{actor['full_name']}» تأیید شد."
                    )
                await ctx.flush_notifications(client)
            else:
                ctx.users_repo.set_session(
                    int(callback_query.author.id),
                    FLOW_REG_REJECT,
                    "REASON",
                    {"req_id": req_id},
                )
                await safe_answer_callback(callback_query, "علت رد درخواست را بنویسید.")
                await client.send_message(
                    callback_query.author.id,
                    f"📝 لطفاً علت رد درخواست ثبت‌نام شماره #{req_id} را ارسال کنید:",
                    reply_markup=wizard_cancel_keyboard(),
                )

        await ctx.run_safe_callback(client, callback_query, _run, sensitive=True)

    ctx.register_session_flow(FLOW_REG, _handle_reg_step)
    ctx.register_session_flow(FLOW_REG_REJECT, _handle_reject_step)


async def _handle_reg_step(ctx: BotContext, client: Client, message: Message, session: dict) -> None:
    uid = int(message.author.id)
    step = session["step"]
    data = session["data"]
    role = data["requested_role"]
    raw = (message.text or "").strip()

    if step == "FULL_NAME":
        data["full_name"] = validate_full_name(raw)
        ctx.users_repo.set_session(uid, FLOW_REG, "PHONE", data)
        req_txt = "الزامی" if role != "STUDENT" else "اختیاری - در صورت نداشتن «ندارد» بفرستید"
        await message.reply(f"📱 لطفاً شماره تلفن همراه خود را وارد کنید ({req_txt}):")
        return

    if step == "PHONE":
        data["phone"] = validate_phone(raw, required=(role != "STUDENT"))
        if role == "STUDENT":
            ctx.users_repo.set_session(uid, FLOW_REG, "GRADE", data)
            await message.reply("🎓 پایه تحصیلی خود را وارد کنید (نمونه: 10 یا یازدهم):")
        elif role == "PARENT":
            ctx.users_repo.set_session(uid, FLOW_REG, "TARGET_STUDENT", data)
            await message.reply("🔢 کد دانش‌آموزی فرزند خود را وارد کنید:")
        elif role == "TEACHER":
            ctx.users_repo.set_session(uid, FLOW_REG, "SPECIALTY", data)
            await message.reply("📘 رشته یا درس تخصصی تدریس خود را وارد کنید:")
        else:
            await _finalize_registration(ctx, client, message, uid, data)
        return

    if step == "GRADE":
        data["grade_level"] = validate_grade_level(raw)
        ctx.users_repo.set_session(uid, FLOW_REG, "CLASS_NAME", data)
        await message.reply("🏫 نام کلاس خود را وارد کنید (یا در صورت نامشخص بودن «ندارد» بفرستید):")
        return

    if step == "CLASS_NAME":
        data["requested_class_name"] = None if raw in {"ندارد", "-"} else raw
        ctx.users_repo.set_session(uid, FLOW_REG, "STUDENT_CODE", data)
        await message.reply("🔢 کد دانش‌آموزی خود را وارد کنید (یا «ندارد» بفرستید):")
        return

    if step == "STUDENT_CODE":
        data["student_code"] = validate_student_code(raw, required=False)
        ctx.users_repo.set_session(uid, FLOW_REG, "PARENT_PHONE", data)
        await message.reply("📞 شماره تماس ولی خود را وارد کنید (یا «ندارد» بفرستید):")
        return

    if step == "PARENT_PHONE":
        data["parent_phone"] = validate_phone(raw, required=False)
        await _finalize_registration(ctx, client, message, uid, data)
        return

    if step == "TARGET_STUDENT":
        data["target_student_code"] = validate_student_code(raw, required=True)
        await _finalize_registration(ctx, client, message, uid, data)
        return

    if step == "SPECIALTY":
        data["specialty"] = raw[:80]
        await _finalize_registration(ctx, client, message, uid, data)


async def _finalize_registration(ctx: BotContext, client: Client, message: Message, uid: int, data: dict) -> None:
    req = ctx.services.registration.submit_registration(
        bale_user_id=uid,
        full_name=data["full_name"],
        requested_role=data["requested_role"],
        phone=data.get("phone"),
        grade_level=data.get("grade_level"),
        requested_class_name=data.get("requested_class_name"),
        student_code=data.get("student_code"),
        parent_phone=data.get("parent_phone"),
        target_student_code=data.get("target_student_code"),
        specialty=data.get("specialty"),
    )
    ctx.users_repo.clear_session(uid)

    # در صورت تعریف گروه مدیریتی، ارسال کارت دارای دکمه‌های تأیید/رد به گروه مدیریتی
    mgmt_gid = ctx.services.registration.get_management_group_id()
    if mgmt_gid:
        # پیام متنی صف را با پیام دکمه‌دار مستقیم در گروه مدیریتی تکمیل می‌کنیم
        ctx.db_clear = ctx.services.notifications.announcements.db.execute(
            "DELETE FROM message_queue WHERE message_type = 'REGISTRATION_MANAGEMENT_GROUP' AND status = 'PENDING' AND text LIKE ?;",
            (f"%#{req['id']}%",),
        )
        try:
            sent_msg = await client.send_message(
                mgmt_gid,
                ctx.services.registration.format_management_request_report(req),
                reply_markup=registration_review_inline(req["id"]),
            )
            if sent_msg and getattr(sent_msg, "id", None):
                ctx.services.registration.reg_repo.set_mgmt_message_id(req["id"], sent_msg.id)
        except Exception:
            # در صورت خطای شبکه، بازگرداندن به صف پایدار برای ارسال خودکار بعدی
            ctx.services.notifications.announcements.enqueue_messages(
                recipients=[{"chat_id": mgmt_gid, "user_id": None}],
                text=ctx.services.registration.format_management_request_report(req),
                message_type="REGISTRATION_MANAGEMENT_GROUP",
            )

    await message.reply(
        f"✅ درخواست ثبت‌نام شما با شماره #{req['id']} ثبت شد و برای گروه مدیریتی مدرسه ارسال گردید.\n"
        f"نتیجه بررسی به محض اعلام مدیریت به اطلاع شما خواهد رسید.",
        reply_markup=unregistered_keyboard(has_pending=True),
    )


async def _handle_reject_step(ctx: BotContext, client: Client, message: Message, session: dict) -> None:
    uid = int(message.author.id)
    actor = ctx.get_actor(uid)
    req_id = int(session["data"]["req_id"])
    reason = (message.text or "").strip()
    ctx.services.registration.reject_registration(actor, req_id, reason)
    ctx.users_repo.clear_session(uid)
    await message.reply(
        f"❌ درخواست ثبت‌نام #{req_id} رد شد و گزارش آن در گروه مدیریتی ثبت گردید.",
        reply_markup=ctx.main_markup_for(actor),
    )
    await ctx.flush_notifications(client)
