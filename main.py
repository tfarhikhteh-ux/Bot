from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys
import threading
from typing import Optional, Sequence, Tuple

from werkzeug.serving import make_server

from config import AppConfig, ConfigurationError
from database import DatabaseManager, ensure_primary_admin, run_migrations
from handlers import BotContext, register_all_handlers
from services import ServiceContainer
from utils.helpers import FatalBotAuthError, SafeSchoolClient
from utils.logging_config import redact_sensitive_text, setup_logging
from utils.security import DuplicateActionGuard, RateLimiter, SingleInstanceError, SingleInstanceLock
from web import create_web_app


def bootstrap_services(config: AppConfig) -> Tuple[ServiceContainer, dict]:
    config.ensure_directories()
    primary_admin_id = config.validate_primary_admin()
    db = DatabaseManager(config.database_path)
    run_migrations(
        db,
        school_name=config.school_name,
        academic_year=config.academic_year,
        current_term=config.current_term,
        management_group_id=config.management_group_id,
    )
    admin_user = ensure_primary_admin(db, primary_admin_id, config.primary_admin_name)
    services = ServiceContainer.build(
        db=db,
        backup_dir=config.backup_dir,
        primary_admin_bale_id=primary_admin_id,
        management_group_id=config.management_group_id,
        backup_retention_count=config.backup_retention_count,
    )
    return services, admin_user


def build_bot_client(config: AppConfig, services: ServiceContainer) -> Tuple[SafeSchoolClient, BotContext]:
    config.validate_bot_settings()
    client = SafeSchoolClient(
        token=config.bale_bot_token,
        time_out=config.bale_timeout_seconds,
        proxy=config.bale_proxy_url,
        base_url=config.bale_api_base_url,
    )
    ctx = BotContext(
        services=services,
        rate_limiter=RateLimiter(
            max_requests=config.rate_limit_max_requests,
            window_seconds=config.rate_limit_window_seconds,
        ),
        dup_guard=DuplicateActionGuard(cooldown_seconds=3.0),
    )
    register_all_handlers(client, ctx)
    return client, ctx


async def background_scheduler_loop(
    client: SafeSchoolClient,
    config: AppConfig,
    services: ServiceContainer,
    stop_event: asyncio.Event,
) -> None:
    log = logging.getLogger("school_bot.scheduler")

    async def _send_msg(chat_id: int, text: str):
        return await client.send_message(chat_id, text)

    while not stop_event.is_set():
        try:
            services.notifications.run_scheduled_tasks_tick(
                backup_service=services.backups,
                auto_backup_interval_hours=config.auto_backup_interval_hours,
            )
            await services.notifications.dispatch_queue_batch(
                send_fn=_send_msg,
                batch_size=config.message_queue_batch_size,
                rate_delay_seconds=config.message_queue_rate_delay_seconds,
            )
        except Exception as exc:
            log.error("خطا در چرخه زمان‌بندی پس‌زمینه: %s", redact_sensitive_text(str(exc)), exc_info=True)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=float(config.scheduler_poll_seconds))
        except asyncio.TimeoutError:
            pass


async def run_bot_async(config: AppConfig, services: ServiceContainer) -> None:
    client, _ = build_bot_client(config, services)
    stop_event = asyncio.Event()
    await client.connect_resilient()
    scheduler_task = asyncio.create_task(background_scheduler_loop(client, config, services, stop_event))
    try:
        await client.poll_forever(stop_event=stop_event)
    finally:
        stop_event.set()
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        if client.connection.is_started:
            await client.disconnect()


def run_web_server(config: AppConfig, services: ServiceContainer) -> None:
    app = create_web_app(config, services)
    server = make_server(config.web_host, config.web_port, app, threaded=True)
    logging.getLogger("school_bot.web").info(
        "پنل وب مدیریتی بر روی http://%s:%s آماده دریافت درخواست است.",
        config.web_host,
        config.web_port,
    )
    server.serve_forever()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="سامانه جامع مدیریت هوشمند مدرسه در بله (School Bot)")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("bot", help="اجرای ربات بله و زمان‌بند خودکار (پیش‌فرض)")
    sub.add_parser("web", help="اجرای پنل وب مدیریتی")
    sub.add_parser("all", help="اجرای هم‌زمان ربات بله و پنل وب")
    sub.add_parser("migrate", help="ساخت پایگاه داده و اجرای Migrationها")
    sub.add_parser("backup", help="تهیه نسخه پشتیبان دستی از دیتابیس")

    p_restore = sub.add_parser("restore", help="بازیابی دیتابیس از فایل پشتیبان")
    p_restore.add_argument("--file", required=True, help="نام فایل پشتیبان در پوشه backups")
    p_restore.add_argument("--confirm", action="store_true", help="تأیید صریح عملیات بازیابی")

    p_pw = sub.add_parser("create-web-admin", help="تنظیم رمز عبور پنل وب برای ادمین اصلی")
    p_pw.add_argument("--password", required=False, help="رمز عبور جدید (حداقل ۸ کاراکتر)")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    cmd = args.command or "bot"

    try:
        config = AppConfig.load()
        config.ensure_directories()
        logger = setup_logging(
            config.log_dir,
            level=config.log_level,
            secrets_to_redact=[config.bale_bot_token, config.web_secret_key],
        )
        services, admin_user = bootstrap_services(config)
    except ConfigurationError as exc:
        sys.stderr.write(f"[خطای تنظیمات امنیتی] {exc}\n")
        return 2

    try:
        if cmd == "migrate":
            logger.info("پایگاه داده و Migrationها با موفقیت بررسی و اعمال شدند.")
            return 0

        if cmd == "backup":
            rec = services.backups.create_backup(actor=admin_user, backup_type="MANUAL")
            logger.info("پشتیبان ایجاد شد: %s (SHA256=%s)", rec["filename"], rec["sha256"])
            return 0

        if cmd == "restore":
            res = services.backups.restore_backup(admin_user, args.file, confirmed=bool(args.confirm))
            logger.info("بازیابی موفق از %s (پشتیبان ایمنی قبل: %s)", res["restored_from"], res["pre_restore_snapshot"])
            return 0

        if cmd == "create-web-admin":
            pw = args.password or getpass.getpass("رمز عبور جدید پنل وب برای ادمین اصلی (حداقل ۸ کاراکتر): ")
            services.registration.set_web_password(admin_user, admin_user["id"], pw)
            logger.info("رمز عبور پنل وب برای ادمین اصلی (%s) با موفقیت ذخیره شد.", admin_user["bale_user_id"])
            return 0

        if cmd == "web":
            run_web_server(config, services)
            return 0

        lock_path = config.log_dir / "school_bot.instance.lock"
        with SingleInstanceLock(lock_path):
            if cmd == "all":
                web_thread = threading.Thread(
                    target=run_web_server,
                    args=(config, services),
                    daemon=True,
                    name="SchoolBotWebServer",
                )
                web_thread.start()
            asyncio.run(run_bot_async(config, services))
        return 0
    except SingleInstanceError as exc:
        logger.error(str(exc))
        return 3
    except (ConfigurationError, FatalBotAuthError) as exc:
        logger.critical("توقف ایمن به علت خطای احراز هویت یا تنظیمات: %s", redact_sensitive_text(str(exc)))
        return 2
    except KeyboardInterrupt:
        logger.info("درخواست توقف توسط کاربر دریافت شد. خروج ایمن انجام شد.")
        return 0
    except Exception as exc:
        logger.critical("خطای بحرانی سامانه: %s", redact_sensitive_text(str(exc)), exc_info=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
