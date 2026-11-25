PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bale_user_id INTEGER NOT NULL UNIQUE,
    username TEXT,
    full_name TEXT NOT NULL,
    phone TEXT,
    role TEXT NOT NULL CHECK (role IN (
        'ADMIN',
        'SCHOOL_MANAGER',
        'ASSOCIATION_MEMBER',
        'DEPUTY',
        'TEACHER',
        'STUDENT',
        'PARENT'
    )),
    student_code TEXT UNIQUE,
    national_code TEXT,
    grade_level TEXT,
    parent_phone TEXT,
    personnel_code TEXT,
    specialty TEXT,
    web_password_hash TEXT,
    custom_permissions TEXT NOT NULL DEFAULT '[]',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    failed_web_logins INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_role_active ON users(role, is_active);
CREATE INDEX IF NOT EXISTS idx_users_full_name ON users(full_name);

CREATE TABLE IF NOT EXISTS parent_students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    student_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    relation TEXT NOT NULL DEFAULT 'والد',
    created_at TEXT NOT NULL,
    UNIQUE(parent_user_id, student_user_id),
    CHECK(parent_user_id != student_user_id)
);

CREATE INDEX IF NOT EXISTS idx_parent_students_student ON parent_students(student_user_id);

CREATE TABLE IF NOT EXISTS registration_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bale_user_id INTEGER NOT NULL,
    full_name TEXT NOT NULL,
    requested_role TEXT NOT NULL CHECK (requested_role IN (
        'STUDENT',
        'PARENT',
        'TEACHER',
        'DEPUTY',
        'ASSOCIATION_MEMBER'
    )),
    phone TEXT,
    grade_level TEXT,
    requested_class_name TEXT,
    student_code TEXT,
    parent_phone TEXT,
    target_student_code TEXT,
    specialty TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')),
    reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TEXT,
    rejection_reason TEXT,
    mgmt_message_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_pending_registration
    ON registration_requests(bale_user_id) WHERE status = 'PENDING';
CREATE INDEX IF NOT EXISTS idx_registration_status ON registration_requests(status, created_at);

CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    grade_level TEXT NOT NULL,
    academic_year TEXT NOT NULL,
    capacity INTEGER NOT NULL DEFAULT 35 CHECK (capacity > 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(name, academic_year)
);

CREATE INDEX IF NOT EXISTS idx_classes_year_active ON classes(academic_year, is_active);

CREATE TABLE IF NOT EXISTS class_enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE RESTRICT,
    student_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    academic_year TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    enrolled_at TEXT NOT NULL,
    left_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_enrollment_per_year
    ON class_enrollments(student_user_id, academic_year) WHERE is_active = 1;
CREATE INDEX IF NOT EXISTS idx_enrollments_class_active ON class_enrollments(class_id, is_active);

CREATE TABLE IF NOT EXISTS class_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER REFERENCES classes(id) ON DELETE SET NULL,
    from_class_id INTEGER REFERENCES classes(id) ON DELETE SET NULL,
    to_class_id INTEGER REFERENCES classes(id) ON DELETE SET NULL,
    student_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT UNIQUE,
    grade_level TEXT NOT NULL DEFAULT 'عمومی',
    subject_type TEXT NOT NULL DEFAULT 'عمومی' CHECK (subject_type IN ('عمومی', 'تخصصی', 'کارگاهی', 'فوق‌برنامه', 'آزمایشگاه')),
    default_weekly_periods INTEGER NOT NULL DEFAULT 2 CHECK (default_weekly_periods > 0),
    default_coefficient REAL NOT NULL DEFAULT 1.0 CHECK (default_coefficient > 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE(name, grade_level)
);

CREATE TABLE IF NOT EXISTS class_subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE RESTRICT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE RESTRICT,
    teacher_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    weekly_periods INTEGER NOT NULL DEFAULT 2 CHECK (weekly_periods > 0),
    coefficient REAL NOT NULL DEFAULT 1.0 CHECK (coefficient > 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE(class_id, subject_id)
);

CREATE INDEX IF NOT EXISTS idx_class_subjects_teacher ON class_subjects(teacher_user_id, is_active);

CREATE TABLE IF NOT EXISTS bell_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_number INTEGER NOT NULL UNIQUE CHECK (period_number BETWEEN 1 AND 12),
    title TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS timetable_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    day_of_week INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    period_number INTEGER NOT NULL REFERENCES bell_periods(period_number) ON DELETE RESTRICT,
    week_type TEXT NOT NULL DEFAULT 'BOTH' CHECK (week_type IN ('BOTH', 'WEEK_1', 'WEEK_2')),
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE RESTRICT,
    teacher_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    room TEXT,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(class_id, day_of_week, period_number, week_type)
);

CREATE INDEX IF NOT EXISTS idx_timetable_teacher_slot ON timetable_entries(teacher_user_id, day_of_week, period_number);

CREATE TABLE IF NOT EXISTS timetable_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    day_of_week INTEGER NOT NULL,
    period_number INTEGER NOT NULL,
    week_type TEXT NOT NULL,
    action TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attendance_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE RESTRICT,
    student_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    att_date TEXT NOT NULL,
    period_number INTEGER NOT NULL DEFAULT 1 CHECK (period_number BETWEEN 1 AND 12),
    status TEXT NOT NULL CHECK (status IN (
        'PRESENT',
        'ABSENT',
        'LATE',
        'EARLY_LEAVE',
        'EXCUSED_ABSENT',
        'UNEXCUSED_ABSENT'
    )),
    note TEXT,
    recorded_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    update_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(class_id, student_user_id, att_date, period_number)
);

CREATE INDEX IF NOT EXISTS idx_attendance_date_class ON attendance_records(att_date, class_id);
CREATE INDEX IF NOT EXISTS idx_attendance_student_date ON attendance_records(student_user_id, att_date);

CREATE TABLE IF NOT EXISTS attendance_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attendance_id INTEGER NOT NULL REFERENCES attendance_records(id) ON DELETE CASCADE,
    old_status TEXT,
    new_status TEXT NOT NULL,
    reason TEXT,
    changed_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS disciplinary_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    class_id INTEGER REFERENCES classes(id) ON DELETE SET NULL,
    note_date TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    recorded_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    term TEXT NOT NULL DEFAULT 'ترم اول',
    min_score REAL NOT NULL DEFAULT 0.0,
    max_score REAL NOT NULL DEFAULT 20.0,
    weight REAL NOT NULL DEFAULT 1.0 CHECK (weight > 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    CHECK (max_score > min_score)
);

CREATE TABLE IF NOT EXISTS grades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE RESTRICT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE RESTRICT,
    teacher_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    evaluation_type_id INTEGER NOT NULL REFERENCES evaluation_types(id) ON DELETE RESTRICT,
    academic_term TEXT NOT NULL,
    score REAL NOT NULL,
    min_score REAL NOT NULL DEFAULT 0.0,
    max_score REAL NOT NULL DEFAULT 20.0,
    coefficient REAL NOT NULL DEFAULT 1.0 CHECK (coefficient > 0),
    note TEXT,
    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (score >= min_score AND score <= max_score)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_active_grade
    ON grades(student_user_id, class_id, subject_id, evaluation_type_id, academic_term)
    WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_grades_class_subject ON grades(class_id, subject_id, is_deleted);

CREATE TABLE IF NOT EXISTS grade_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grade_id INTEGER NOT NULL REFERENCES grades(id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK (action IN ('CREATE', 'UPDATE', 'DELETE')),
    old_score REAL,
    new_score REAL,
    reason TEXT,
    changed_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS homework_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    teacher_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    due_date TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('ALL', 'ROLE', 'CLASS', 'TEACHERS', 'PARENTS', 'MANAGEMENT_GROUP')),
    target_value TEXT,
    scheduled_at TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'QUEUED', 'SENDING', 'SENT', 'PARTIAL', 'FAILED', 'DELETED')),
    created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    sent_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_announcements_sched ON announcements(status, scheduled_at);

CREATE TABLE IF NOT EXISTS message_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    announcement_id INTEGER REFERENCES announcements(id) ON DELETE SET NULL,
    recipient_chat_id INTEGER NOT NULL,
    recipient_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    message_type TEXT NOT NULL DEFAULT 'NOTIFICATION',
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'SENT', 'FAILED')),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_retry_at TEXT,
    last_error TEXT,
    sent_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_message_queue_pending ON message_queue(status, next_retry_at);

CREATE TABLE IF NOT EXISTS holidays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    holiday_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    title TEXT NOT NULL,
    holiday_type TEXT NOT NULL CHECK (holiday_type IN ('OFFICIAL', 'SCHOOL', 'EMERGENCY', 'EVENT')),
    is_full_close INTEGER NOT NULL DEFAULT 1 CHECK (is_full_close IN (0, 1)),
    description TEXT,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    notified INTEGER NOT NULL DEFAULT 0 CHECK (notified IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE(holiday_date, title),
    CHECK (end_date >= holiday_date)
);

CREATE INDEX IF NOT EXISTS idx_holidays_dates ON holidays(holiday_date, end_date);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'GENERAL',
    admin_only INTEGER NOT NULL DEFAULT 0 CHECK (admin_only IN (0, 1)),
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL UNIQUE,
    file_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    backup_type TEXT NOT NULL CHECK (backup_type IN ('MANUAL', 'SCHEDULED', 'PRE_RESTORE')),
    integrity_ok INTEGER NOT NULL DEFAULT 1 CHECK (integrity_ok IN (0, 1)),
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    task_key TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    last_run_slot TEXT,
    last_run_at TEXT,
    status TEXT NOT NULL DEFAULT 'OK',
    details TEXT
);

CREATE TABLE IF NOT EXISTS user_sessions (
    bale_user_id INTEGER PRIMARY KEY,
    flow TEXT NOT NULL,
    step TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    actor_bale_id INTEGER,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    before_data TEXT,
    after_data TEXT,
    result TEXT NOT NULL DEFAULT 'SUCCESS' CHECK (result IN ('SUCCESS', 'DENIED', 'FAILURE')),
    ip_address TEXT,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_created_action ON audit_logs(created_at DESC, action);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs(actor_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS continuous_modifiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    teacher_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    academic_term TEXT NOT NULL,
    delta REAL NOT NULL CHECK (delta != 0),
    old_score REAL NOT NULL,
    new_score REAL NOT NULL,
    note TEXT,
    source_chat_id INTEGER,
    source_message_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_continuous_mod_unique_msg
    ON continuous_modifiers(source_chat_id, source_message_id)
    WHERE source_chat_id IS NOT NULL AND source_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_continuous_mod_student
    ON continuous_modifiers(student_user_id, academic_term, subject_id);

