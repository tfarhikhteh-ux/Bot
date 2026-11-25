from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable, Optional

BOT_TOKEN_REGEX = re.compile(r"(?:bot)?\d{5,20}:[A-Za-z0-9_\-]{15,}")
SENSITIVE_KV_REGEX = re.compile(
    r"((?:password|passwd|secret|token|web_secret_key)\s*[=:]\s*)([^\s,;\"']+)",
    re.IGNORECASE,
)


def redact_sensitive_text(value: str, extra_secrets: Optional[Iterable[str]] = None) -> str:
    if not value:
        return value
    cleaned = BOT_TOKEN_REGEX.sub("[REDACTED_TOKEN]", value)
    cleaned = SENSITIVE_KV_REGEX.sub(r"\1[REDACTED]", cleaned)
    if extra_secrets:
        for secret in extra_secrets:
            if secret and len(secret) >= 6 and secret in cleaned:
                cleaned = cleaned.replace(secret, "[REDACTED]")
    return cleaned


class SecretRedactingFilter(logging.Filter):
    """جلوگیری کامل از ثبت توکن ربات و کلیدهای امنیتی در تمام لاگ‌ها."""

    def __init__(self, extra_secrets: Optional[Iterable[str]] = None) -> None:
        super().__init__()
        self._secrets = [s for s in (extra_secrets or []) if s and len(s) >= 6]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_sensitive_text(record.msg, self._secrets)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: redact_sensitive_text(str(v), self._secrets) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_sensitive_text(a, self._secrets) if isinstance(a, str) else a
                    for a in record.args
                )
        return True


def setup_logging(
    log_dir: Path,
    level: str = "INFO",
    secrets_to_redact: Optional[Iterable[str]] = None,
) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "school_bot.log"

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    for existing in list(root_logger.handlers):
        root_logger.removeHandler(existing)
        existing.close()

    redactor = SecretRedactingFilter(secrets_to_redact)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redactor)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(redactor)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # جلوگیری از ثبت URL حاوی توکن توسط httpx و httpcore
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("balethon.network.connection").setLevel(logging.WARNING)

    return logging.getLogger("school_bot")
