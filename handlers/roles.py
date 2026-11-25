from __future__ import annotations

from balethon import Client
from balethon.conditions import equals, private
from balethon.objects import CallbackQuery, InlineKeyboard, InlineKeyboardButton, Message

from keyboards.admin import BTN_ROLES_MGMT, role_selection_inline
from keyboards.main import confirm_inline, wizard_cancel_keyboard
from services.permission_service import ASSOCIATION_DELEGATABLE_PERMISSIONS
from utils.formatters import role_label
from utils.helpers import safe_answer_callback
from utils.validators import ValidationError

from . import BotContext, cb_prefix

FLOW_ROLE = "ROLE_MGMT_FLOW"


def register(client: Client, ctx: BotContext) -> None:
    @client.on_message(private & equals(BTN_ROLES_MGMT))
    async def roles_menu(*, client: Client, message: Message):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "MANAGE_USERS", "RBAC")
            counts = ctx.users_repo.count_by_role(active_only=True)
            lines = ["🔐 مدیریت نقش‌ها و مجوزهای دسترسی (RBAC)", "━" * 24]
            for r_code in ["ADMIN", "SCHOOL_MANAGER", "DEPUTY", "ASSOCIATION_MEMBER", "TEACHER", "STUDENT", "PARENT"]:
                lines.append(f"▫️ {role_label(r_code)}: {counts.get(r_code, 0)} نفر")
            kb = InlineKeyboard(
                [InlineKeyboardButton("🎖 تغییر نقش کاربر با شناسه", callback_data="role:start_change")],
                [InlineKeyboardButton("🤝 تنظیم مجوز عضو انجمن", callback_data="role:assoc_perms")],
            )
            await message.reply("\n".join(lines), reply_markup=kb)

        await ctx.run_safe_message(client, message, _run)

    @client.on_callback_query(cb_prefix("role:"))
    async def role_callbacks(*, client: Client, callback_query: CallbackQuery):
        async def _run(actor):
            ctx.services.permissions.require_permission(actor, "MANAGE_USERS", "RBAC")
            parts = callback_query.data.split(":")
            action = parts[1]
            uid = int(callback_query.author.id)
            chat_id = callback_query.message.chat.id

            if action == "start_change":
                ctx.users_repo.set_session(uid, FLOW_ROLE, "FIND_USER_FOR_ROLE", {})
                await safe_answer_callback(callback_query, "شناسه بله را وارد کنید.")
                await client.send_message(
                    chat_id,
                    "🎖 شناسه عددی بله کاربری که می‌خواهید نقش او را تغییر دهید وارد کنید:",
                    reply_markup=wizard_cancel_keyboard(),
                )
            elif action == "pick":
                target_id = int(parts[2])
                target = ctx.users_repo.get_by_id(target_id)
                if not target:
                    raise ValidationError("کاربر یافت نشد.")
                include_admin = actor["role"] == "ADMIN"
                await safe_answer_callback(callback_query, "انتخاب نقش جدید")
                await client.send_message(
                    chat_id,
                    f"👤 کاربر: {target['full_name']} (نقش فعلی: {role_label(target['role'])})\nنقش جدید را انتخاب کنید:",
                    reply_markup=role_selection_inline(target_id, include_admin_roles=include_admin),
                )
            elif action == "ask":
                target_id, new_role = int(parts[2]), parts[3]
                target = ctx.users_repo.get_by_id(target_id)
                if not target:
                    raise ValidationError("کاربر یافت نشد.")
                ctx.services.permissions.ensure_can_manage_user(actor, target_user=target, target_new_role=new_role)
                await safe_answer_callback(callback_query, "تأیید تغییر نقش")
                await client.send_message(
                    chat_id,
                    f"⚠️ آیا از تغییر نقش «{target['full_name']}» به «{role_label(new_role)}» اطمینان دارید؟",
                    reply_markup=confirm_inline(f"role:confirm:{target_id}:{new_role}"),
                )
            elif action == "confirm":
                target_id, new_role = int(parts[2]), parts[3]
                updated = ctx.services.registration.change_user_role(actor, target_id, new_role)
                await safe_answer_callback(callback_query, "نقش تغییر یافت.")
                await callback_query.message.edit_text(
                    f"✅ نقش «{updated['full_name']}» با موفقیت به «{role_label(updated['role'])}» تغییر یافت."
                )
            elif action == "assoc_perms":
                members = ctx.users_repo.search(role="ASSOCIATION_MEMBER", active_only=True, limit=15)
                await safe_answer_callback(callback_query, "اعضای انجمن")
                if not members:
                    await client.send_message(chat_id, "در حال حاضر کاربری با نقش «عضو انجمن» وجود ندارد.")
                    return
                kb = InlineKeyboard()
                for m in members:
                    kb.add_row(InlineKeyboardButton(f"🤝 {m['full_name']}", callback_data=f"role:assoc_user:{m['id']}"))
                await client.send_message(chat_id, "عضو انجمن موردنظر را انتخاب کنید:", reply_markup=kb)
            elif action == "assoc_user":
                m_id = int(parts[2])
                member = ctx.users_repo.get_by_id(m_id)
                if not member:
                    raise ValidationError("عضو انجمن یافت نشد.")
                cur_perms = set(ctx.users_repo.get_custom_permissions(member))
                kb = InlineKeyboard()
                for p_name in sorted(ASSOCIATION_DELEGATABLE_PERMISSIONS):
                    mark = "✅" if p_name in cur_perms else "⬜"
                    kb.add_row(
                        InlineKeyboardButton(f"{mark} {p_name}", callback_data=f"role:toggle_perm:{m_id}:{p_name}")
                    )
                await safe_answer_callback(callback_query, "مجوزهای عضو انجمن")
                await client.send_message(
                    chat_id,
                    f"🤝 تنظیم مجوزهای تکمیلی برای «{member['full_name']}»:",
                    reply_markup=kb,
                )
            elif action == "toggle_perm":
                m_id, perm_code = int(parts[2]), parts[3]
                member = ctx.users_repo.get_by_id(m_id)
                if not member:
                    raise ValidationError("عضو انجمن یافت نشد.")
                cur_perms = set(ctx.users_repo.get_custom_permissions(member))
                if perm_code in cur_perms:
                    cur_perms.remove(perm_code)
                else:
                    cur_perms.add(perm_code)
                ctx.services.registration.grant_association_permissions(actor, m_id, sorted(cur_perms))
                await safe_answer_callback(callback_query, "مجوز به‌روزرسانی شد.")
                await callback_query.message.edit_text(
                    f"✅ مجوزهای «{member['full_name']}» به‌روزرسانی شد: {', '.join(sorted(cur_perms)) or 'بدون مجوز اضافی'}"
                )

        await ctx.run_safe_callback(client, callback_query, _run, sensitive=True)

    ctx.register_session_flow(FLOW_ROLE, _handle_role_step)


async def _handle_role_step(ctx: BotContext, client: Client, message: Message, session: dict) -> None:
    uid = int(message.author.id)
    actor = ctx.services.permissions.require_permission(ctx.get_actor(uid), "MANAGE_USERS", "RBAC")
    raw = (message.text or "").strip()
    if not raw.isdigit():
        raise ValidationError("شناسه بله باید عددی باشد.")
    target = ctx.users_repo.get_by_bale_id(int(raw))
    if not target:
        raise ValidationError("کاربری با این شناسه بله یافت نشد.")
    ctx.users_repo.clear_session(uid)
    await message.reply(
        f"👤 کاربر: {target['full_name']} (نقش فعلی: {role_label(target['role'])})\nنقش جدید را انتخاب کنید:",
        reply_markup=role_selection_inline(target["id"], include_admin_roles=(actor["role"] == "ADMIN")),
    )
