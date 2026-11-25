from __future__ import annotations

import io

from flask import Blueprint, flash, g, redirect, render_template, request, send_file, url_for

from services import AccessDeniedError
from utils.validators import ValidationError
from web.auth import get_services, permission_required

operations_bp = Blueprint("operations", __name__)


@operations_bp.route("/announcements", methods=["GET", "POST"])
@permission_required("MANAGE_ANNOUNCEMENTS")
def announcements_page():
    svc = get_services()
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "create":
                svc.notifications.create_announcement(
                    actor=g.user,
                    title=request.form.get("title", ""),
                    body=request.form.get("body", ""),
                    target_type=request.form.get("target_type", "ALL"),
                    target_value=request.form.get("target_value") or None,
                    scheduled_at=request.form.get("scheduled_at") or None,
                )
                flash("اطلاعیه با موفقیت ثبت و در صف ارسال قرار گرفت.", "success")
            elif action == "edit":
                svc.notifications.edit_announcement(
                    actor=g.user,
                    announcement_id=int(request.form["announcement_id"]),
                    title=request.form.get("title", ""),
                    body=request.form.get("body", ""),
                )
                flash("اطلاعیه ویرایش شد.", "success")
            elif action == "retry":
                cnt = svc.notifications.announcements.retry_failed_for_announcement(int(request.form["announcement_id"]))
                flash(f"{cnt} پیام ناموفق در صف تلاش مجدد قرار گرفت.", "info")
            elif action == "delete":
                svc.notifications.delete_announcement(g.user, int(request.form["announcement_id"]))
                flash("اطلاعیه حذف شد.", "info")
        except (ValidationError, AccessDeniedError, ValueError) as exc:
            flash(str(exc), "danger")
        return redirect(url_for("operations.announcements_page"))

    items = svc.notifications.announcements.list_announcements(include_management=True, limit=50)
    classes = svc.timetable.classes.list_classes(active_only=True)
    return render_template("announcements.html", announcements=items, classes=classes)


@operations_bp.route("/holidays", methods=["GET", "POST"])
@permission_required("MANAGE_HOLIDAYS")
def holidays_page():
    svc = get_services()
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "create":
                h_type = request.form.get("holiday_type", "OFFICIAL")
                svc.notifications.create_holiday(
                    actor=g.user,
                    holiday_date=request.form.get("holiday_date", ""),
                    end_date=request.form.get("end_date") or None,
                    title=request.form.get("title", ""),
                    holiday_type=h_type,
                    is_full_close=(h_type != "EVENT"),
                    description=request.form.get("description"),
                    notify_all=(request.form.get("notify_all") == "1"),
                )
                flash("مورد تقویمی جدید ثبت شد.", "success")
            elif action == "delete":
                svc.notifications.delete_holiday(g.user, int(request.form["holiday_id"]))
                flash("مورد تقویمی حذف شد.", "info")
        except (ValidationError, AccessDeniedError, ValueError) as exc:
            flash(str(exc), "danger")
        return redirect(url_for("operations.holidays_page"))

    holidays = svc.notifications.holidays.list_holidays(limit=100)
    return render_template("holidays.html", holidays=holidays)


@operations_bp.route("/reports")
@permission_required("VIEW_REPORTS_ALL")
def reports_page():
    svc = get_services()
    date_from = (request.args.get("date_from") or "").strip() or None
    date_to = (request.args.get("date_to") or "").strip() or None
    class_id = request.args.get("class_id", type=int)
    overview = None
    try:
        overview = svc.reports.get_dashboard_overview(g.user, date_from=date_from, date_to=date_to, class_id=class_id)
    except ValidationError as exc:
        flash(str(exc), "danger")
        overview = svc.reports.get_dashboard_overview(g.user)

    classes = svc.timetable.classes.list_classes(active_only=True)
    return render_template(
        "reports.html",
        overview=overview,
        classes=classes,
        date_from=date_from or "",
        date_to=date_to or "",
        selected_class_id=class_id,
    )


@operations_bp.route("/reports/export")
@permission_required("VIEW_REPORTS_ALL")
def export_report_route():
    svc = get_services()
    category = (request.args.get("category") or "OVERVIEW").upper()
    fmt = (request.args.get("fmt") or "csv").lower()
    class_id = request.args.get("class_id", type=int)
    role = (request.args.get("role") or "").strip() or None
    date_from = (request.args.get("date_from") or "").strip() or None
    date_to = (request.args.get("date_to") or "").strip() or None

    data = svc.reports.export_report(
        actor=g.user,
        category=category,
        fmt=fmt,
        class_id=class_id,
        role=role,
        date_from=date_from,
        date_to=date_to,
    )
    mime = "application/pdf" if fmt == "pdf" else "text/csv; charset=utf-8"
    ext = "pdf" if fmt == "pdf" else "csv"
    return send_file(
        io.BytesIO(data),
        mimetype=mime,
        as_attachment=True,
        download_name=f"report_{category.lower()}.{ext}",
    )


@operations_bp.route("/audit")
@permission_required("VIEW_AUDIT_LOGS")
def audit_page():
    svc = get_services()
    action = (request.args.get("action") or "").strip() or None
    result = (request.args.get("result") or "").strip() or None
    logs = svc.audit.search_logs(action=action, result=result, limit=150)
    return render_template("audit.html", logs=logs, action_filter=action or "", result_filter=result or "")


@operations_bp.route("/settings", methods=["GET", "POST"])
@permission_required("MANAGE_SCHOOL_SETTINGS")
def settings_page():
    svc = get_services()
    is_admin = g.user["role"] == "ADMIN"

    if request.method == "POST":
        form_action = request.form.get("form_action")
        try:
            if form_action == "update_setting":
                svc.notifications.update_setting(
                    g.user,
                    request.form["key"],
                    request.form.get("value", ""),
                )
                flash("تنظیم ذخیره شد.", "success")
            elif form_action == "create_backup":
                rec = svc.backups.create_backup(actor=g.user, backup_type="MANUAL")
                flash(f"نسخه پشتیبان {rec['filename']} با موفقیت ایجاد شد.", "success")
            elif form_action == "restore_backup":
                confirmed = request.form.get("confirm_restore") == "1"
                res = svc.backups.restore_backup(g.user, request.form["filename"], confirmed=confirmed)
                flash(f"دیتابیس با موفقیت از {res['restored_from']} بازیابی شد.", "success")
        except (ValidationError, AccessDeniedError, ValueError) as exc:
            flash(str(exc), "danger")
        return redirect(url_for("operations.settings_page"))

    settings_rows = svc.notifications.settings.list_all(include_admin_only=is_admin)
    backups = svc.backups.list_backups(g.user) if is_admin else []
    return render_template("settings.html", settings_rows=settings_rows, backups=backups, is_admin=is_admin)
