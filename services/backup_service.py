from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from database.db import DatabaseManager
from database.migrations import ensure_primary_admin, run_migrations
from database.repositories.settings import SettingsRepository
from utils.dates import now_iso, now_tehran
from utils.security import restrict_file_permissions
from utils.validators import ValidationError, validate_backup_filename

from .audit_service import AuditService
from .permission_service import PermissionService


class BackupService:
    """پشتیبان‌گیری آنلاین سازگار با SQLite، اعتبارسنجی سلامت فایل، نگهداری چرخشی و بازیابی امن."""

    def __init__(
        self,
        db: DatabaseManager,
        permissions: PermissionService,
        backup_dir: Path,
        default_retention: int = 10,
    ) -> None:
        self.db = db
        self.permissions = permissions
        self.backup_dir = Path(backup_dir).resolve()
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.default_retention = default_retention
        self.settings = SettingsRepository(db)
        self.audit = AuditService(db)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            while chunk := fh.read(65536):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def verify_sqlite_backup_file(path: Path) -> bool:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        try:
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0)
            try:
                chk = conn.execute("PRAGMA integrity_check;").fetchone()
                if not chk or str(chk[0]).lower() != "ok":
                    return False
                tbl = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='users';"
                ).fetchone()
                return tbl is not None
            finally:
                conn.close()
        except sqlite3.Error:
            return False

    def create_backup(
        self,
        actor: Optional[Dict[str, Any]] = None,
        backup_type: str = "MANUAL",
    ) -> Dict[str, Any]:
        if backup_type == "MANUAL" and actor is not None:
            self.permissions.require_permission(actor, "BACKUP_CREATE", "BACKUP")

        stamp = now_tehran().strftime("%Y%m%d_%H%M%S_%f")
        prefix = backup_type.lower()
        filename = f"school_backup_{prefix}_{stamp}.db"
        dest_path = (self.backup_dir / filename).resolve()

        with self.db._write_lock:
            src_conn = self.db.get_connection()
            dst_conn = sqlite3.connect(str(dest_path))
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()

        restrict_file_permissions(dest_path)
        integrity_ok = self.verify_sqlite_backup_file(dest_path)
        if not integrity_ok:
            dest_path.unlink(missing_ok=True)
            raise RuntimeError("بررسی صحت فایل پشتیبان تولیدشده ناموفق بود.")

        sha256 = self._sha256_file(dest_path)
        size_bytes = dest_path.stat().st_size
        ts = now_iso()
        actor_id = actor["id"] if actor else None

        bid = self.db.execute(
            """
            INSERT INTO backups(filename, file_path, size_bytes, sha256, backup_type, integrity_ok, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?);
            """,
            (filename, str(dest_path), size_bytes, sha256, backup_type, actor_id, ts),
        )
        record = self.db.fetchone("SELECT * FROM backups WHERE id = ?;", (bid,))

        self.audit.log(
            action="CREATE_BACKUP",
            entity_type="BACKUP",
            actor=actor,
            entity_id=filename,
            after_data={"filename": filename, "size_bytes": size_bytes, "type": backup_type, "sha256": sha256},
            description=f"تهیه نسخه پشتیبان {backup_type} ({filename})",
        )

        self._prune_old_backups()
        return record

    def _prune_old_backups(self) -> int:
        row_setting = self.db.fetchone("SELECT value, updated_by FROM settings WHERE key = 'backup_retention_count';")
        if row_setting and row_setting["updated_by"] is not None:
            retention = self.settings.get_int("backup_retention_count", self.default_retention)
        else:
            retention = self.default_retention
        retention = max(retention, 1)
        rows = self.db.fetchall("SELECT * FROM backups ORDER BY id DESC;")
        if len(rows) <= retention:
            return 0
        removed = 0
        for stale in rows[retention:]:
            p = Path(stale["file_path"])
            try:
                if p.exists() and p.resolve().parent == self.backup_dir:
                    p.unlink()
            except OSError:
                pass
            self.db.execute("DELETE FROM backups WHERE id = ?;", (stale["id"],))
            removed += 1
        return removed

    def list_backups(self, actor: Dict[str, Any]) -> List[Dict[str, Any]]:
        self.permissions.require_permission(actor, "BACKUP_CREATE", "BACKUP")
        return self.db.fetchall(
            """
            SELECT b.*, u.full_name AS creator_name
            FROM backups b
            LEFT JOIN users u ON u.id = b.created_by
            ORDER BY b.id DESC;
            """
        )

    def resolve_safe_backup_path(self, filename: str) -> Path:
        clean_name = validate_backup_filename(filename)
        candidate = (self.backup_dir / clean_name).resolve()
        if candidate.parent != self.backup_dir or not candidate.is_file():
            raise ValidationError("فایل پشتیبان موردنظر در پوشه امن پشتیبان‌ها یافت نشد.")
        return candidate

    def restore_backup(
        self,
        actor: Dict[str, Any],
        filename: str,
        confirmed: bool = False,
    ) -> Dict[str, Any]:
        self.permissions.require_permission(actor, "BACKUP_RESTORE", "BACKUP", filename)
        if not confirmed:
            raise ValidationError("عملیات بازیابی نیازمند تأیید صریح است تا از بازنویسی تصادفی دیتابیس جلوگیری شود.")

        src_path = self.resolve_safe_backup_path(filename)
        if not self.verify_sqlite_backup_file(src_path):
            raise ValidationError("فایل پشتیبان انتخاب‌شده معیوب است و امکان بازیابی ندارد.")

        # تهیه پشتیبان ایمنی پیش از بازیابی
        pre_snapshot = self.create_backup(actor=actor, backup_type="PRE_RESTORE")

        with self.db._write_lock:
            live_conn = self.db.get_connection()
            src_conn = sqlite3.connect(f"file:{src_path.as_posix()}?mode=ro", uri=True)
            try:
                src_conn.backup(live_conn)
                live_conn.execute("PRAGMA foreign_keys = ON;")
            finally:
                src_conn.close()

        run_migrations(self.db)
        if self.permissions.primary_admin_bale_id:
            ensure_primary_admin(self.db, self.permissions.primary_admin_bale_id)

        self.audit.log(
            action="RESTORE_BACKUP",
            entity_type="BACKUP",
            actor=actor,
            entity_id=filename,
            after_data={"restored_from": filename, "pre_restore_snapshot": pre_snapshot["filename"]},
            description=f"بازیابی پایگاه داده از فایل {filename} توسط {actor['full_name']}",
        )
        return {"restored_from": filename, "pre_restore_snapshot": pre_snapshot["filename"]}
