from __future__ import annotations

from balethon import Client
from balethon.conditions import equals, private, text
from balethon.objects import Message

from keyboards.main import BTN_CANCEL, BTN_MAIN_MENU, unregistered_keyboard
from utils.formatters import role_label
from utils.helpers import safe_answer_callback

from . import BotContext, cb_prefix


def register(client: Client, ctx: BotContext) -> None:
    @client.on_message(private & equals(BTN_CANCEL, BTN_MAIN_MENU, "/لغو", "/منو", "/cancel"))
    async def cancel_and_home(*, client: Client, message: Message):
        async def _run(actor):
            uid = int(message.author.id)
            ctx.users_repo.clear_session(uid)
            if actor and actor.get("is_active"):
                await message.reply(
                    "🏠 عملیات جاری لغو شد و به منوی اصلی بازگشتید.",
                    reply_markup=ctx.main_markup_for(actor),
                )
            else:
                pending = ctx.services.registration.reg_repo.get_pending_by_bale_id(uid)
                await message.reply(
                    "🏠 عملیات جاری لغو شد و به صفحه اصلی بازگشتید.",
                    reply_markup=unregistered_keyboard(has_pending=bool(pending)),
                )

        await ctx.run_safe_message(client, message, _run)

    @client.on_callback_query(cb_prefix("nav:cancel"))
    async def cancel_inline_callback(*, client: Client, callback_query):
        async def _run(actor):
            ctx.users_repo.clear_session(int(callback_query.author.id))
            await safe_answer_callback(callback_query, "عملیات لغو شد.")
            if callback_query.message:
                await callback_query.message.edit_text("❌ عملیات لغو شد.")

        await ctx.run_safe_callback(client, callback_query, _run)

    @client.on_command(name="start")
    async def start_command(*_args, client: Client, message: Message):
        async def _run(actor):
            uid = int(message.author.id)
            ctx.users_repo.clear_session(uid)
            school_name = ctx.services.notifications.settings.get("school_name", "مدرسه")
            academic_year = ctx.services.notifications.settings.get("academic_year", "1405-1406")

            if not message.chat or str(message.chat.type).lower().endswith("group"):
                await message.reply(
                    f"🏫 ربات مدیریت هوشمند {school_name} فعال است.\n"
                    f"شناسه این گروه: {message.chat.id}"
                )
                return

            if actor:
                if not actor.get("is_active"):
                    await message.reply(
                        "⛔ حساب کاربری شما در حال حاضر غیرفعال است.\n"
                        "لطفاً برای بررسی وضعیت با مدیریت مدرسه تماس بگیرید."
                    )
                    return

                ctx.services.audit.log(
                    action="BOT_START",
                    entity_type="SESSION",
                    actor=actor,
                    description="ورود کاربر به منوی اصلی ربات",
                )
                await message.reply(
                    f"سلام «{actor['full_name']}» گرامی 👋\n"
                    f"به سامانه جامع «{school_name}» (سال تحصیلی {academic_year}) خوش آمدید.\n"
                    f"🎖 نقش کاربری شما: {role_label(actor['role'])}\n\n"
                    f"از دکمه‌های منوی پایین یا دستور /دستورها استفاده نمایید.",
                    reply_markup=ctx.main_markup_for(actor),
                )
                return

            pending = ctx.services.registration.reg_repo.get_pending_by_bale_id(uid)
            if pending:
                await message.reply(
                    f"⏳ سلام «{pending['full_name']}» گرامی؛\n"
                    f"درخواست ثبت‌نام شما (شماره #{pending['id']}) با نقش «{role_label(pending['requested_role'])}» "
                    f"در انتظار بررسی گروه مدیریتی مدرسه است.\n"
                    f"به محض تأیید یا بررسی، نتیجه از همین طریق به شما اطلاع داده می‌شود.",
                    reply_markup=unregistered_keyboard(has_pending=True),
                )
                return

            await message.reply(
                f"سلام! به سامانه هوشمند «{school_name}» خوش آمدید 🌺\n\n"
                "شما هنوز در سامانه ثبت‌نام نشده‌اید.\n"
                "برای ارسال درخواست عضویت بر روی دکمه «📝 شروع ثبت‌نام» بزنید.",
                reply_markup=unregistered_keyboard(has_pending=False),
            )

        await ctx.run_safe_message(client, message, _run)


def register_session_dispatcher(client: Client, ctx: BotContext) -> None:
    @client.on_message(private & text)
    async def dispatch_active_wizard(*, client: Client, message):
        async def _run(actor):
            uid = int(message.author.id)
            session = ctx.users_repo.get_session(uid)
            if not session:
                if actor and actor.get("is_active"):
                    await message.reply(
                        "لطفاً یکی از گزینه‌های منو را انتخاب کنید یا برای مشاهده راهنما دستور /دستورها را بفرستید.",
                        reply_markup=ctx.main_markup_for(actor),
                    )
                else:
                    pending = ctx.services.registration.reg_repo.get_pending_by_bale_id(uid)
                    await message.reply(
                        "برای شروع کار از دستور /start استفاده کنید.",
                        reply_markup=unregistered_keyboard(has_pending=bool(pending)),
                    )
                return

            flow_handler = ctx.session_routers.get(session["flow"])
            if not flow_handler:
                ctx.users_repo.clear_session(uid)
                await message.reply("وضعیت فرم قبلی منقضی شد. لطفاً مجدداً از منوی اصلی اقدام کنید.")
                return
            await flow_handler(ctx, client, message, session)

        await ctx.run_safe_message(client, message, _run)
