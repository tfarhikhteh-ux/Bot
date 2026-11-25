from __future__ import annotations

from pathlib import Path
import pytest

from services import AccessDeniedError
from utils.logging_config import redact_sensitive_text
from utils.security import SingleInstanceError, SingleInstanceLock
from utils.validators import ValidationError


def test_backup_create_verify_retention_and_restore(app_env, actors):
    svc = app_env["services"]
    admin = actors["admin"]
    mgr = actors["manager"]

    # مدیر مدرسه مجاز به پشتیبان‌گیری یا بازیابی نیست
    with pytest.raises(AccessDeniedError):
        svc.backups.create_backup(actor=mgr)

    # ایجاد پشتیبان در وضعیت اولیه
    b1 = svc.backups.create_backup(actor=admin)
    assert Path(b1["file_path"]).is_file()
    assert svc.backups.verify_sqlite_backup_file(Path(b1["file_path"])) is True

    # افزودن یک کلاس جدید بعد از پشتیبان اول
    extra_cls = svc.timetable.create_class(mgr, "کلاس-بعد-از-پشتیبان", "12")
    assert svc.timetable.classes.get_by_id(extra_cls["id"]) is not None

    # بازیابی بدون تأیید صریح مسدود می‌شود
    with pytest.raises(ValidationError):
        svc.backups.restore_backup(admin, b1["filename"], confirmed=False)

    # بازیابی با تأیید ادمین: دیتابیس به نقطه b1 برمی‌گردد و یک PRE_RESTORE هم ساخته می‌شود
    restore_info = svc.backups.restore_backup(admin, b1["filename"], confirmed=True)
    assert restore_info["restored_from"] == b1["filename"]
    assert svc.timetable.classes.get_by_id(extra_cls["id"]) is None

    # بررسی حذف خودکار نسخه‌های قدیمی طبق BACKUP_RETENTION_COUNT=4
    for _ in range(5):
        svc.backups.create_backup(actor=admin, backup_type="SCHEDULED")
    all_backups = svc.backups.list_backups(admin)
    assert len(all_backups) <= 4


def test_token_redaction_and_single_instance_lock(tmp_path):
    raw_log = "Request to https://tapi.bale.ai/bot12345678:SecretTokenValue123456789/getUpdates failed"
    cleaned = redact_sensitive_text(raw_log)
    assert "SecretTokenValue" not in cleaned
    assert "[REDACTED_TOKEN]" in cleaned

    lock_file = tmp_path / "bot.lock"
    with SingleInstanceLock(lock_file):
        with pytest.raises(SingleInstanceError):
            with SingleInstanceLock(lock_file):
                pass
