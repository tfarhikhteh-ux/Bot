from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

PLACEHOLDER_SECRETS = {
    "",
    "your_bale_bot_token_here",
    "changeme",
    "change_this_to_a_long_random_secret_key_at_least_32_chars",
}

TOKEN_PATTERN = re.compile(r"^\d{5,20}:[A-Za-z0-9_\-]{15,}$")


class ConfigurationError(RuntimeError):
    """خطای تنظیمات حیاتی که باید مانع اجرای ناامن سامانه شود."""


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_optional_int(value: Optional[str], field_name: str) -> Optional[int]:
    if value is None or value.strip() == "":
        return None
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ConfigurationError(f"مقدار متغیر {field_name} باید یک عدد صحیح معتبر باشد.") from exc


def _parse_int(value: Optional[str], default: int, field_name: str, minimum: Optional[int] = None) -> int:
    parsed = _parse_optional_int(value, field_name)
    result = default if parsed is None else parsed
    if minimum is not None and result < minimum:
        raise ConfigurationError(f"مقدار {field_name} نباید کمتر از {minimum} باشد.")
    return result


def _parse_float(value: Optional[str], default: float, field_name: str, minimum: float = 0.0) -> float:
    if value is None or value.strip() == "":
        return default
    try:
        result = float(value.strip())
    except ValueError as exc:
        raise ConfigurationError(f"مقدار متغیر {field_name} باید عددی باشد.") from exc
    if result < minimum:
        raise ConfigurationError(f"مقدار {field_name} نباید کمتر از {minimum} باشد.")
    return result


@dataclass(frozen=True)
class AppConfig:
    bale_bot_token: str
    primary_admin_id: Optional[int]
    primary_admin_name: str
    management_group_id: Optional[int]
    database_path: Path
    backup_dir: Path
    log_dir: Path
    log_level: str
    school_name: str
    academic_year: str
    current_term: str
    timezone: str
    backup_retention_count: int
    auto_backup_interval_hours: int
    rate_limit_max_requests: int
    rate_limit_window_seconds: int
    message_queue_batch_size: int
    message_queue_rate_delay_seconds: float
    scheduler_poll_seconds: int
    web_host: str
    web_port: int
    web_secret_key: str
    web_session_minutes: int
    web_cookie_secure: bool
    web_max_login_attempts: int
    web_lockout_minutes: int
    bale_api_base_url: str
    bale_timeout_seconds: int
    bale_proxy_url: Optional[str]

    @classmethod
    def from_mapping(cls, env: Mapping[str, str], root_dir: Path = BASE_DIR) -> "AppConfig":
        def resolve_path(raw: str) -> Path:
            p = Path(raw)
            return p if p.is_absolute() else (root_dir / p).resolve()

        primary_admin_id = _parse_optional_int(env.get("PRIMARY_ADMIN_ID"), "PRIMARY_ADMIN_ID")
        if primary_admin_id is not None and primary_admin_id <= 0:
            raise ConfigurationError("شناسه ادمین اصلی (PRIMARY_ADMIN_ID) باید عددی مثبت و معتبر باشد.")

        management_group_id = _parse_optional_int(env.get("MANAGEMENT_GROUP_ID"), "MANAGEMENT_GROUP_ID")
        if management_group_id == 0:
            raise ConfigurationError("شناسه گروه مدیریتی (MANAGEMENT_GROUP_ID) نامعتبر است.")

        proxy_raw = (env.get("BALE_PROXY_URL") or "").strip() or None

        return cls(
            bale_bot_token=(env.get("BALE_BOT_TOKEN") or "").strip(),
            primary_admin_id=primary_admin_id,
            primary_admin_name=(env.get("PRIMARY_ADMIN_NAME") or "مدیر ارشد سامانه").strip(),
            management_group_id=management_group_id,
            database_path=resolve_path((env.get("DATABASE_PATH") or "school.db").strip()),
            backup_dir=resolve_path((env.get("BACKUP_DIR") or "backups").strip()),
            log_dir=resolve_path((env.get("LOG_DIR") or "logs").strip()),
            log_level=(env.get("LOG_LEVEL") or "INFO").strip().upper(),
            school_name=(env.get("SCHOOL_NAME") or "دبیرستان نمونه").strip(),
            academic_year=(env.get("ACADEMIC_YEAR") or "1405-1406").strip(),
            current_term=(env.get("CURRENT_TERM") or "ترم اول").strip(),
            timezone=(env.get("TIMEZONE") or "Asia/Tehran").strip(),
            backup_retention_count=_parse_int(env.get("BACKUP_RETENTION_COUNT"), 10, "BACKUP_RETENTION_COUNT", 1),
            auto_backup_interval_hours=_parse_int(
                env.get("AUTO_BACKUP_INTERVAL_HOURS"), 24, "AUTO_BACKUP_INTERVAL_HOURS", 1
            ),
            rate_limit_max_requests=_parse_int(env.get("RATE_LIMIT_MAX_REQUESTS"), 25, "RATE_LIMIT_MAX_REQUESTS", 3),
            rate_limit_window_seconds=_parse_int(
                env.get("RATE_LIMIT_WINDOW_SECONDS"), 60, "RATE_LIMIT_WINDOW_SECONDS", 5
            ),
            message_queue_batch_size=_parse_int(
                env.get("MESSAGE_QUEUE_BATCH_SIZE"), 15, "MESSAGE_QUEUE_BATCH_SIZE", 1
            ),
            message_queue_rate_delay_seconds=_parse_float(
                env.get("MESSAGE_QUEUE_RATE_DELAY_SECONDS"), 0.15, "MESSAGE_QUEUE_RATE_DELAY_SECONDS", 0.0
            ),
            scheduler_poll_seconds=_parse_int(env.get("SCHEDULER_POLL_SECONDS"), 30, "SCHEDULER_POLL_SECONDS", 5),
            web_host=(env.get("WEB_HOST") or "127.0.0.1").strip(),
            web_port=_parse_int(env.get("WEB_PORT"), 8080, "WEB_PORT", 1),
            web_secret_key=(env.get("WEB_SECRET_KEY") or "").strip(),
            web_session_minutes=_parse_int(env.get("WEB_SESSION_MINUTES"), 60, "WEB_SESSION_MINUTES", 5),
            web_cookie_secure=_parse_bool(env.get("WEB_COOKIE_SECURE"), False),
            web_max_login_attempts=_parse_int(env.get("WEB_MAX_LOGIN_ATTEMPTS"), 5, "WEB_MAX_LOGIN_ATTEMPTS", 2),
            web_lockout_minutes=_parse_int(env.get("WEB_LOCKOUT_MINUTES"), 15, "WEB_LOCKOUT_MINUTES", 1),
            bale_api_base_url=(env.get("BALE_API_BASE_URL") or "https://tapi.bale.ai").strip().rstrip("/"),
            bale_timeout_seconds=_parse_int(env.get("BALE_TIMEOUT_SECONDS"), 25, "BALE_TIMEOUT_SECONDS", 5),
            bale_proxy_url=proxy_raw,
        )

    @classmethod
    def load(cls, env_file: Optional[Path] = None) -> "AppConfig":
        target_env = env_file or (BASE_DIR / ".env")
        if target_env.exists():
            load_dotenv(dotenv_path=target_env, override=False, encoding="utf-8")
        return cls.from_mapping(os.environ, root_dir=BASE_DIR)

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def validate_primary_admin(self) -> int:
        if self.primary_admin_id is None or self.primary_admin_id <= 0:
            raise ConfigurationError(
                "خطای امنیتی: شناسه ادمین اصلی (PRIMARY_ADMIN_ID) در فایل .env تنظیم نشده است. "
                "سامانه برای جلوگیری از اعطای دسترسی تصادفی متوقف شد."
            )
        return self.primary_admin_id

    def validate_bot_settings(self) -> None:
        self.validate_primary_admin()
        token_lower = self.bale_bot_token.lower()
        if not self.bale_bot_token or token_lower in PLACEHOLDER_SECRETS:
            raise ConfigurationError("توکن ربات بله (BALE_BOT_TOKEN) در فایل .env تنظیم نشده است.")
        if not TOKEN_PATTERN.match(self.bale_bot_token):
            raise ConfigurationError("قالب توکن ربات بله (BALE_BOT_TOKEN) نامعتبر است.")

    def validate_web_settings(self) -> None:
        self.validate_primary_admin()
        secret_lower = self.web_secret_key.lower()
        if not self.web_secret_key or secret_lower in PLACEHOLDER_SECRETS or len(self.web_secret_key) < 24:
            raise ConfigurationError(
                "کلید امنیتی پنل وب (WEB_SECRET_KEY) تنظیم نشده یا کوتاه‌تر از ۲۴ کاراکتر است. "
                "لطفاً یک کلید تصادفی امن در فایل .env وارد کنید."
            )
