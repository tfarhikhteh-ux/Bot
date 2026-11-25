from __future__ import annotations

from balethon import Client
from balethon.conditions import equals, private
from balethon.objects import CallbackQuery, InlineKeyboard, InlineKeyboardButton, Message

from keyboards.admin import BTN_ANNOUNCEMENTS_MGMT
from keyboards.main import confirm_inline, wizard_cancel_keyboard
from keyboards.manager import BTN_VIEW_ANNOUNCEMENTS, announcements_mgmt_inline
from utils.dates import format_jalali_datetime
from utils.formatters import ANNOUNCEMENT_TARGET_LABELS
from utils.helpers import safe_answer_callback, send_chunked_message

from . import BotContext, cb_prefix

FLOW_ANN = "ANNOUNCEMENT_FLOW"


def register(client: Client, ctx: BotContext) -> None:
    @client.on_message(private & equals(BTN_ANNOUNCEMENTS_MGMT))
    async def announcements_mgmt_btn(*, client: Client, message: Message):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "MANAGE_ANNOUNCEMENTS", "ANNOUNCEMENT")
            await message.reply("📢 مدیریت اطلاعیه‌ها و صف ارسال گروهی:", reply_markup=announcements_mgmt_inline())

        await ctx.run_safe_message(client, message, _run)

    @client.on_message(private & equals(BTN_VIEW_ANNOUNCEMENTS))
    async def view_announcements_btn(*, client: Client, message: Message):
        async def _run(actor):
            checked = ctx.services.permissions.require_permission(actor, "VIEW_ANNOUNCEMENTS", "ANNOUNCEMENT")
            cls_ids = []
            if checked["role"] == "STUDENT":
                cls = ctx.services.timetable.classes.get_student_active_class(checked["id"])
                if cls:
                    cls_ids.append(cls["id"])
            elif checked["role"] == "PARENT":
                for ch in ctx.users_repo.get_children_of_parent(checked["id"]):
                    cls = ctx.services.timetable.classes.get_student_active_class(ch["id"])
                    if cls:
                        cls_ids.append(cls["id"])
            anns = ctx.services.notifications.announcements.list_announcements(
                for_role=checked["role"], for_class_ids=cls_ids, limit=10
            )
            if not anns:
                await message.reply("📭 اطلاعیه جدیدی ثبت نشده است.")
                return
            lines = ["📢 آخرین اطلاعیه‌های مدرسه:", "━" * 24]
            for a in anns:
                lines.append(f"📌 {a['title']} ({format_jalali_datetime(a['created_at'])})\n{a['body']}\n")
            await send_chunked_message(client, message.chat.id, "\n".join(lines))

        await ctx.run_safe_message(client, message, _run)

    @client.on_callback_query(cb_prefix("ann:"))
    async def ann_callbacks(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "MANAGE_ANNOUNCEMENTS", "ANNOUNCEMENT")
            parts = callback_query.data.split(":")
            action = parts[1]
            uid = int(callback_query.author.id)
            chat_id = callback_query.message.chat.id

            if action == "create":
                kb = InlineKeyboard(
                    [
                        InlineKeyboardButton("🌐 تمام کاربران", callback_data="ann:target:ALL"),
                        InlineKeyboardButton("👨‍🏫 دبیران", callback_data="ann:target:TEACHERS"),
                    ],
                    [
                        InlineKeyboardButton("👪 والدین", callback_data="ann:target:PARENTS"),
                        InlineKeyboardButton("🏫 یک کلاس مشخص", callback_data="ann:target:CLASS"),
                    ],
                    [
                        InlineKeyboardButton("🎖 یک نقش مشخص", callback_data="ann:target:ROLE"),
                        InlineKeyboardButton("🛡 گروه مدیریتی", callback_data="ann:target:MANAGEMENT_GROUP"),
                    ],
                )
                await safe_answer_callback(callback_query, "انتخاب مخاطبان")
                await client.send_message(chat_id, "🎯 جامعه هدف اطلاعیه را انتخاب کنید:", reply_markup=kb)
            elif action == "target":
                t_type = parts[2]
                data = {"target_type": t_type}
                if t_type in {"CLASS", "ROLE"}:
                    ctx.users_repo.set_session(uid, FLOW_ANN, "TARGET_VALUE", data)
                    prompt = "🏫 شناسه عددی کلاس موردنظر را وارد کنید:" if t_type == "CLASS" else "🎖 کد نقش را وارد کنید (STUDENT, PARENT, TEACHER, DEPUTY):"
                    await safe_answer_callback(callback_query, "انتخاب جزئیات مقصد")
                    await client.send_message(chat_id, prompt, reply_markup=wizard_cancel_keyboard())
                else:
                    ctx.users_repo.set_session(uid, FLOW_ANN, "TITLE", data)
                    await safe_answer_callback(callback_query, "عنوان اطلاعیه")
                    await client.send_message(chat_id, "📝 عنوان اطلاعیه را وارد کنید:", reply_markup=wizard_cancel_keyboard())
            elif action == "history":
                anns = ctx.services.notifications.announcements.list_announcements(include_management=True, limit=10)
                await safe_answer_callback(callback_query, f"{len(anns)} اطلاعیه")
                if not anns:
                    await client.send_message(chat_id, "اطلاعیه‌ای ثبت نشده است.")
                    return
                for a in anns[:6]:
                    kb = InlineKeyboard(
                        [
                            InlineKeyboardButton("🔁 تلاش مجدد ناموفق‌ها", callback_data=f"ann:retry:{a['id']}"),
                            InlineKeyboardButton("🗑 حذف اطلاعیه", callback_data=f"ann:ask_del:{a['id']}"),
                        ]
                    )
                    await client.send_message(
                        chat_id,
                        f"📢 #{a['id']} {a['title']} [{a['status']}]\n"
                        f"🎯 مقصد: {ANNOUNCEMENT_TARGET_LABELS.get(a['target_type'], a['target_type'])}\n"
                        f"✅ موفق: {a['sent_count']} | ❌ ناموفق: {a['failed_count']} | ⏳ در صف: {a['pending_count']}",
                        reply_markup=kb,
                    )
            elif action == "retry":
                aid = int(parts[2])
                cnt = ctx.services.notifications.announcements.retry_failed_for_announcement(aid)
                await safe_answer_callback(callback_query, f"{cnt} پیام در صف تلاش مجدد قرار گرفت.")
                await ctx.flush_notifications(client)
            elif action == "ask_del":
                aid = int(parts[2])
                await safe_answer_callback(callback_query, "تأیید حذف")
                await client.send_message(
                    chat_id,
                    f"⚠️ آیا از حذف اطلاعیه #{aid} اطمینان دارید؟",
                    reply_markup=confirm_inline(f"ann:do_del:{aid}"),
                )
            elif action == "do_del":
                aid = int(parts[2])
                ctx.services.notifications.delete_announcement(actor, aid)
                await safe_answer_callback(callback_query, "حذف شد.")
                await callback_query.message.edit_text(f"✅ اطلاعیه #{aid} حذف شد.")

        await ctx.run_safe_callback(client, callback_query, _run, sensitive=True)

    ctx.register_session_flow(FLOW_ANN, _handle_ann_step)


async def _handle_ann_step(ctx: BotContext, client: Client, message: Message, session: dict) -> None:
    uid = int(message.author.id)
    actor = ctx.services.permissions.require_permission(ctx.get_actor(uid), "MANAGE_ANNOUNCEMENTS", "ANNOUNCEMENT")
    step = session["step"]
    data = session["data"]
    raw = (message.text or "").strip()

    if step == "TARGET_VALUE":
        data["target_value"] = raw
        ctx.users_repo.set_session(uid, FLOW_ANN, "TITLE", data)
        await message.reply("📝 عنوان اطلاعیه را وارد کنید:")
        return

    if step == "TITLE":
        data["title"] = raw
        ctx.users_repo.set_session(uid, FLOW_ANN, "BODY", data)
        await message.reply("📄 متن کامل اطلاعیه را وارد کنید:")
        return

    if step == "BODY":
        data["body"] = raw
        ctx.users_repo.set_session(uid, FLOW_ANN, "SCHEDULE", data)
        await message.reply(
            "⏰ برای ارسال فوری کلمه «فوری» را بفرستید، یا زمان ارسال را به صورت «۱۴۰۵/۰۸/۱۰ ۰۸:۳۰» وارد کنید:"
        )
        return

    if step == "SCHEDULE":
        sched = None if raw in {"فوری", "الان", "-"} else raw
        ann = ctx.services.notifications.create_announcement(
            actor=actor,
            title=data["title"],
            body=data["body"],
            target_type=data["target_type"],
            target_value=data.get("target_value"),
            scheduled_at=sched,
        )
        ctx.users_repo.clear_session(uid)
        await message.reply(
            f"✅ اطلاعیه «{ann['title']}» با وضعیت «{ann['status']}» ثبت شد.",
            reply_markup=ctx.main_markup_for(actor),
        )
        if sched is None:
            await ctx.flush_notifications(client)
