from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from database.db import DatabaseManager

from .attendance_service import AttendanceService
from .audit_service import AuditService
from .backup_service import BackupService
from .grade_service import GradeService
from .moderation_service import ModerationService
from .notification_service import NotificationService
from .permission_service import AccessDeniedError, PermissionService
from .registration_service import RegistrationService
from .report_service import ReportService
from .timetable_service import TimetableService


@dataclass
class ServiceContainer:
    db: DatabaseManager
    audit: AuditService
    permissions: PermissionService
    registration: RegistrationService
    timetable: TimetableService
    attendance: AttendanceService
    grades: GradeService
    notifications: NotificationService
    backups: BackupService
    reports: ReportService
    moderation: ModerationService

    @classmethod
    def build(
        cls,
        db: DatabaseManager,
        backup_dir: Path,
        primary_admin_bale_id: Optional[int] = None,
        management_group_id: Optional[int] = None,
        backup_retention_count: int = 10,
    ) -> "ServiceContainer":
        audit = AuditService(db)
        permissions = PermissionService(db, primary_admin_bale_id=primary_admin_bale_id)
        registration = RegistrationService(db, permissions, management_group_id=management_group_id)
        timetable = TimetableService(db, permissions)
        attendance = AttendanceService(db, permissions)
        grades = GradeService(db, permissions)
        notifications = NotificationService(db, permissions, management_group_id=management_group_id)
        backups = BackupService(db, permissions, backup_dir=backup_dir, default_retention=backup_retention_count)
        reports = ReportService(db, permissions, grades)
        moderation = ModerationService(db, permissions)
        return cls(
            db=db,
            audit=audit,
            permissions=permissions,
            registration=registration,
            timetable=timetable,
            attendance=attendance,
            grades=grades,
            notifications=notifications,
            backups=backups,
            reports=reports,
            moderation=moderation,
        )


__all__ = [
    "AccessDeniedError",
    "AttendanceService",
    "AuditService",
    "BackupService",
    "GradeService",
    "ModerationService",
    "NotificationService",
    "PermissionService",
    "RegistrationService",
    "ReportService",
    "ServiceContainer",
    "TimetableService",
]
