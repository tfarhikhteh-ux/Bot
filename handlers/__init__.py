from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional

from balethon import Client
from balethon.conditions import Condition
from balethon.objects import CallbackQuery, Message

from database.repositories.users import UserRepository
from keyboards.main import build_main_menu
from services import AccessDeniedError, ServiceContainer
from utils.helpers import safe_answer_callback
from utils.logging_config import redact_sensitive_text
from utils.security import DuplicateActionGuard, RateLimiter
from utils.validators import ValidationError

log = logging.getLogger("school_bot.handlers")

SessionStepHandler = Callable[["BotContext", Client, Message, Dict[str, Any]], Awaitable[None]]


def cb_prefix(*prefixes: str) -> Condition:
    class _PrefixCondition(Condition):
        def __init__(self) -> None:
            super().__init__(can_process=CallbackQuery)

        async def __call__(self, client, event: CallbackQuery) -> bool:
            data = event.data or ""
            return any(data.startswith(p) for p in prefixes)

    return _PrefixCondition()


@dataclass
class BotContext:
    services: ServiceContainer
    rate_limiter: RateLimiter
    dup_guard: DuplicateActionGuard
    web_secret_key: str = ""
    session_routers: Dict[str, SessionStepHandler] = field(default_factory=dict)

    @property
    def users_repo(self) -> UserRepository:
        return self.services.registration.users

    def get_actor(self, bale_user_id: int) -> Optional[Dict[str, Any]]:
        return self.users_repo.get_by_bale_id(bale_user_id)

    def register_session_flow(self, flow_name: str, handler: SessionStepHandler) -> None:
        self.session_routers[flow_name] = handler

    def main_markup_for(self, actor: Optional[Dict[str, Any]]):
        perms = self.services.permissions.get_effective_permissions(actor) if actor else set()
        return build_main_menu(actor, perms)

    async def flush_notifications(self, client: Client) -> None:
        async def _send(chat_id: int, text: str):
            return await client.send_message(chat_id, text)

        try:
            await self.services.notifications.dispatch_queue_batch(_send, batch_size=15, rate_delay_seconds=0.05)
        except Exception as exc:
            log.warning("خطا در تخلیه فوری صف پیام: %s", redact_sensitive_text(str(exc)))

    async def run_safe_message(
        self,
        client: Client,
        message: Message,
        fn: Callable[[Optional[Dict[str, Any]]], Awaitable[None]],
    ) -> None:
        if not message.author:
            return
        uid = int(message.author.id)
        if not self.rate_limiter.allow(f"msg:{uid}"):
            await message.reply("⚠️ تعداد درخواست‌های شما بیش از حد مجاز است. لطفاً چند ثانیه صبر کنید.")
            return
        actor = self.get_actor(uid)
        try:
            await fn(actor)
        except AccessDeniedError as exc:
            await message.reply(f"⛔ {exc}")
        except ValidationError as exc:
            await message.reply(f"⚠️ {exc}")
        except Exception as exc:
            log.error("خطای پیش‌بینی‌نشده در پردازش پیام کاربر %s: %s", uid, redact_sensitive_text(str(exc)), exc_info=True)
            await message.reply("❌ خطایی در انجام عملیات رخ داد. جزئیات فنی در سیستم ثبت شد؛ لطفاً مجدداً تلاش کنید.")

    async def run_safe_callback(
        self,
        client: Client,
        callback_query: CallbackQuery,
        fn: Callable[[Optional[Dict[str, Any]]], Awaitable[None]],
        sensitive: bool = False,
    ) -> None:
        if not callback_query.author:
            return
        uid = int(callback_query.author.id)
        if not self.rate_limiter.allow(f"cb:{uid}"):
            await safe_answer_callback(callback_query, "لطفاً کمی صبر کنید.", show_alert=True)
            return
        if sensitive and not self.dup_guard.acquire(f"{uid}:{callback_query.data}"):
            await safe_answer_callback(callback_query, "این عملیات در حال انجام است.", show_alert=False)
            return
        actor = self.get_actor(uid)
        try:
            await fn(actor)
        except AccessDeniedError as exc:
            await safe_answer_callback(callback_query, f"⛔ {exc}", show_alert=True)
        except ValidationError as exc:
            await safe_answer_callback(callback_query, f"⚠️ {exc}", show_alert=True)
        except Exception as exc:
            log.error("خطا در پردازش دکمه %s برای %s: %s", callback_query.data, uid, redact_sensitive_text(str(exc)), exc_info=True)
            await safe_answer_callback(callback_query, "خطایی در پردازش درخواست رخ داد.", show_alert=True)


def register_all_handlers(client: Client, ctx: BotContext) -> None:
    from . import (
        admin,
        announcements,
        attendance,
        backup,
        classes,
        grades,
        help as help_handler,
        holidays,
        registration,
        reports,
        roles,
        start,
        subjects,
        timetable,
        users,
    )

    # ۱. ناوبری سراسری، لغو عملیات و دستور شروع و راهنما
    start.register(client, ctx)
    help_handler.register(client, ctx)
    admin.register(client, ctx)

    # ۲. ماژول‌های عملیاتی و دکمه‌ها
    registration.register(client, ctx)
    users.register(client, ctx)
    roles.register(client, ctx)
    classes.register(client, ctx)
    subjects.register(client, ctx)
    timetable.register(client, ctx)
    attendance.register(client, ctx)
    grades.register(client, ctx)
    announcements.register(client, ctx)
    holidays.register(client, ctx)
    reports.register(client, ctx)
    backup.register(client, ctx)

    # ۳. مسیریاب مرکزی فرم‌های چندمرحله‌ای (در انتهای زنجیره تا دکمه‌ها و دستورات اولویت داشته باشند)
    start.register_session_dispatcher(client, ctx)
