from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from services import AccessDeniedError
from services.permission_service import ASSOCIATION_DELEGATABLE_PERMISSIONS
from utils.validators import ValidationError
from web.auth import get_services, login_required, permission_required

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    svc = get_services()
    active_week = svc.notifications.settings.get_active_week_type()
    school_name = svc.notifications.settings.get("school_name", "مدرسه")
    academic_year = svc.notifications.settings.get("academic_year", "1405-1406")
    overview = None
    recent_logs = []
    if "VIEW_REPORTS_ALL" in g.permissions:
        overview = svc.reports.get_dashboard_overview(g.user)
    if "VIEW_AUDIT_LOGS" in g.permissions:
        recent_logs = svc.audit.search_logs(limit=10)
    return render_template(
        "dashboard.html",
        school_name=school_name,
        academic_year=academic_year,
        active_week=active_week,
        overview=overview,
        recent_logs=recent_logs,
    )


@dashboard_bp.route("/users", methods=["GET", "POST"])
@permission_required("MANAGE_USERS")
def users_page():
    svc = get_services()
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "create_user":
                svc.registration.create_user_direct(
                    actor=g.user,
                    bale_user_id=int(request.form.get("bale_user_id") or "0"),
                    full_name=request.form.get("full_name", ""),
                    role=request.form.get("role", "STUDENT"),
                    phone=request.form.get("phone"),
                    student_code=request.form.get("student_code"),
                    grade_level=request.form.get("grade_level"),
                    web_password=request.form.get("web_password") or None,
                )
                flash("کاربر جدید با موفقیت ایجاد شد.", "success")
            elif action == "change_role":
                svc.registration.change_user_role(
                    g.user,
                    int(request.form["target_user_id"]),
                    request.form["new_role"],
                )
                flash("نقش کاربری با موفقیت به‌روزرسانی شد.", "success")
            elif action == "toggle_active":
                target_id = int(request.form["target_user_id"])
                make_active = request.form.get("is_active") == "1"
                svc.registration.set_user_active(g.user, target_id, make_active)
                flash("وضعیت فعال بودن کاربر تغییر یافت.", "success")
            elif action == "set_password":
                svc.registration.set_web_password(
                    g.user,
                    int(request.form["target_user_id"]),
                    request.form.get("new_password", ""),
                )
                flash("رمز عبور پنل وب با موفقیت ذخیره شد.", "success")
            elif action == "link_parent":
                svc.registration.link_parent_student(
                    g.user,
                    int(request.form["parent_user_id"]),
                    int(request.form["student_user_id"]),
                )
                flash("اتصال ولی به دانش‌آموز ثبت شد.", "success")
            elif action == "approve_reg":
                svc.registration.approve_registration(
                    g.user,
                    int(request.form["req_id"]),
                    assigned_role=request.form.get("assigned_role") or None,
                )
                flash("درخواست ثبت‌نام تأیید شد.", "success")
            elif action == "reject_reg":
                svc.registration.reject_registration(
                    g.user,
                    int(request.form["req_id"]),
                    request.form.get("reason") or "عدم تطابق اطلاعات",
                )
                flash("درخواست ثبت‌نام رد شد.", "info")
            elif action == "assoc_perms":
                perms = request.form.getlist("perms")
                svc.registration.grant_association_permissions(
                    g.user,
                    int(request.form["member_user_id"]),
                    perms,
                )
                flash("مجوزهای عضو انجمن ذخیره شد.", "success")
        except (ValidationError, AccessDeniedError, ValueError) as exc:
            flash(str(exc), "danger")
        return redirect(url_for("dashboard.users_page"))

    q = (request.args.get("q") or "").strip() or None
    role_filter = (request.args.get("role") or "").strip() or None
    users = svc.registration.users.search(query=q, role=role_filter, limit=150)
    pending_regs = svc.registration.reg_repo.list_requests(status="PENDING", limit=50)
    parents = svc.registration.users.search(role="PARENT", active_only=True, limit=100)
    students = svc.registration.users.search(role="STUDENT", active_only=True, limit=200)
    assoc_members = svc.registration.users.search(role="ASSOCIATION_MEMBER", active_only=True, limit=50)
    return render_template(
        "users.html",
        users=users,
        pending_regs=pending_regs,
        parents=parents,
        students=students,
        assoc_members=assoc_members,
        delegatable_perms=sorted(ASSOCIATION_DELEGATABLE_PERMISSIONS),
        q=q or "",
        role_filter=role_filter or "",
    )


@dashboard_bp.route("/classes", methods=["GET", "POST"])
@permission_required("MANAGE_CLASSES")
def classes_page():
    svc = get_services()
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "create":
                svc.timetable.create_class(
                    g.user,
                    name=request.form.get("name", ""),
                    grade_level=request.form.get("grade_level", ""),
                    academic_year=request.form.get("academic_year") or None,
                    capacity=int(request.form.get("capacity") or 35),
                )
                flash("کلاس جدید ایجاد شد.", "success")
            elif action == "enroll":
                svc.timetable.enroll_student_in_class(
                    g.user,
                    int(request.form["class_id"]),
                    int(request.form["student_user_id"]),
                )
                flash("دانش‌آموز در کلاس ثبت شد.", "success")
            elif action == "transfer":
                svc.timetable.transfer_student_between_classes(
                    g.user,
                    int(request.form["student_user_id"]),
                    int(request.form["to_class_id"]),
                    note=request.form.get("note", ""),
                )
                flash("انتقال کلاس دانش‌آموز انجام شد.", "success")
            elif action == "delete":
                res = svc.timetable.delete_class_controlled(g.user, int(request.form["class_id"]))
                flash(
                    "کلاس دارای سوابق بود و غیرفعال شد." if res["mode"] == "ARCHIVED" else "کلاس حذف شد.",
                    "info",
                )
        except (ValidationError, AccessDeniedError, ValueError) as exc:
            flash(str(exc), "danger")
        return redirect(url_for("dashboard.classes_page"))

    classes = svc.timetable.classes.list_classes()
    students = svc.registration.users.search(role="STUDENT", active_only=True, limit=300)
    history = svc.timetable.classes.get_history(limit=25)
    return render_template("classes.html", classes=classes, students=students, history=history)


@dashboard_bp.route("/subjects", methods=["GET", "POST"])
@permission_required("MANAGE_SUBJECTS")
def subjects_page():
    svc = get_services()
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "create":
                svc.timetable.create_subject(
                    g.user,
                    name=request.form.get("name", ""),
                    grade_level=request.form.get("grade_level", "عمومی"),
                    subject_type=request.form.get("subject_type", "عمومی"),
                    code=request.form.get("code") or None,
                    default_weekly_periods=int(request.form.get("default_weekly_periods") or 2),
                    default_coefficient=float(request.form.get("default_coefficient") or 1.0),
                )
                flash("درس جدید تعریف شد.", "success")
            elif action == "assign":
                t_raw = request.form.get("teacher_user_id")
                svc.timetable.assign_subject_to_class(
                    g.user,
                    class_id=int(request.form["class_id"]),
                    subject_id=int(request.form["subject_id"]),
                    teacher_user_id=int(t_raw) if t_raw else None,
                    weekly_periods=int(request.form.get("weekly_periods") or 2),
                    coefficient=float(request.form.get("coefficient") or 1.0),
                )
                flash("درس و دبیر به کلاس تخصیص یافت.", "success")
            elif action == "delete":
                mode = svc.timetable.delete_subject_controlled(g.user, int(request.form["subject_id"]))
                flash("درس بایگانی شد." if mode == "ARCHIVED" else "درس حذف شد.", "info")
        except (ValidationError, AccessDeniedError, ValueError) as exc:
            flash(str(exc), "danger")
        return redirect(url_for("dashboard.subjects_page"))

    subjects = svc.timetable.subjects.list_subjects()
    classes = svc.timetable.classes.list_classes(active_only=True)
    teachers = svc.registration.users.search(role="TEACHER", active_only=True, limit=200)
    assignments = []
    for c in classes:
        assignments.extend(svc.timetable.subjects.list_class_subjects(c["id"]))
    return render_template(
        "subjects.html",
        subjects=subjects,
        classes=classes,
        teachers=teachers,
        assignments=assignments,
    )
