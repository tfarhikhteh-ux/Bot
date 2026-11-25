from .announcements import AnnouncementRepository
from .attendance import AttendanceRepository
from .audit import AuditRepository
from .classes import ClassRepository
from .grades import GradeRepository
from .holidays import HolidayRepository
from .registration import RegistrationRepository
from .settings import SettingsRepository
from .subjects import SubjectRepository
from .timetable import TimetableRepository
from .users import UserRepository

__all__ = [
    "AnnouncementRepository",
    "AttendanceRepository",
    "AuditRepository",
    "ClassRepository",
    "GradeRepository",
    "HolidayRepository",
    "RegistrationRepository",
    "SettingsRepository",
    "SubjectRepository",
    "TimetableRepository",
    "UserRepository",
]
