from __future__ import annotations

import io

from flask import Blueprint, flash, g, redirect, render_template, request, send_file, url_for

from services import AccessDeniedError
from utils.dates import PERSIAN_WEEKDAYS, today_iso
from utils.validators import ValidationError
from web.auth import get_services, login_required

academic_bp = Blueprint("academic", __name__)


@academic_bp.route("/timetable", methods=["GET", "POST"])
@login_required
def timetable_page():
    svc = get_services()
    can_manage = "MANAGE_TIMETABLE" in g.permissions

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "set_slot":
                t_raw = request.form.get("teacher_user_id")
                svc.timetable.set_timetable_slot(
                    actor=g.user,
                    class_id=int(request.form["class_id"]),
                    day_of_week=int(request.form["day_of_week"]),
                    period_number=int(request.form["period_number"]),
                    week_type=request.form.get("week_type", "BOTH"),
                    subject_id=int(request.form["subject_id"]),
                    teacher_user_id=int(t_raw) if t_raw else None,
                    replace_existing=True,
                )
                flash("خانه برنامه هفتگی با موفقیت ثبت شد.", "success")
            elif action == "clear_slot":
                svc.timetable.clear_timetable_slot(
                    actor=g.user,
                    class_id=int(request.form["class_id"]),
                    day_of_week=int(request.form["day_of_week"]),
                    period_number=int(request.form["period_number"]),
                    week_type=request.form.get("week_type", "ALL"),
                )
                flash("خانه برنامه پاک شد.", "info")
            elif action == "bell_period":
                svc.timetable.configure_bell_period(
                    actor=g.user,
                    period_number=int(request.form["period_number"]),
                    title=request.form.get("title", ""),
                    start_time=request.form.get("start_time", ""),
                    end_time=request.form.get("end_time", ""),
                )
                flash("تنظیمات زنگ ذخیره شد.", "success")
        except (ValidationError, AccessDeniedError, ValueError) as exc:
            flash(str(exc), "danger")
        return redirect(url_for("academic.timetable_page", class_id=request.form.get("class_id")))

    if g.user["role"] == "TEACHER":
        classes = svc.timetable.classes.list_teacher_classes(g.user["id"])
    else:
        classes = svc.timetable.classes.list_classes(active_only=True)

    selected_class_id = request.args.get("class_id", type=int) or (classes[0]["id"] if classes else None)
    week_filter = (request.args.get("week_type") or "").strip().upper() or None
    entries = []
    if selected_class_id:
        try:
            svc.permissions.ensure_can_access_class(g.user, selected_class_id)
            entries = svc.timetable.timetable.get_class_timetable(selected_class_id, week_type=week_filter)
        except AccessDeniedError as exc:
            flash(str(exc), "danger")

    teacher_entries = []
    if g.user["role"] == "TEACHER":
        teacher_entries = svc.timetable.timetable.get_teacher_timetable(g.user["id"], week_type=week_filter)

    periods = svc.timetable.timetable.list_bell_periods()
    subjects = svc.timetable.subjects.list_subjects(active_only=True)
    teachers = svc.registration.users.search(role="TEACHER", active_only=True, limit=200)
    conflicts = svc.timetable.timetable.detect_all_conflicts() if can_manage else []
    return render_template(
        "timetable.html",
        classes=classes,
        selected_class_id=selected_class_id,
        week_filter=week_filter or "",
        entries=entries,
        teacher_entries=teacher_entries,
        periods=periods,
        subjects=subjects,
        teachers=teachers,
        weekdays=PERSIAN_WEEKDAYS,
        conflicts=conflicts,
        can_manage=can_manage,
    )


@academic_bp.route("/timetable/export/<int:class_id>/<fmt>")
@login_required
def export_timetable(class_id: int, fmt: str):
    svc = get_services()
    wt = request.args.get("week_type") or None
    if fmt == "png":
        data = svc.timetable.export_class_timetable_png(g.user, class_id, week_type=wt)
        return send_file(io.BytesIO(data), mimetype="image/png", as_attachment=True, download_name=f"timetable_{class_id}.png")
    if fmt == "xlsx":
        data = svc.timetable.export_class_timetable_xlsx(g.user, class_id, week_type=wt)
        return send_file(
            io.BytesIO(data),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"timetable_{class_id}.xlsx",
        )
    data = svc.timetable.export_class_timetable_pdf(g.user, class_id, week_type=wt)
    return send_file(io.BytesIO(data), mimetype="application/pdf", as_attachment=True, download_name=f"timetable_{class_id}.pdf")


@academic_bp.route("/grades/report-card/<int:student_id>/<fmt>")
@login_required
def export_report_card(student_id: int, fmt: str):
    svc = get_services()
    fmt_clean = "xlsx" if fmt.lower() == "xlsx" else "pdf"
    data = svc.grades.export_student_report_card(g.user, student_id, fmt=fmt_clean)
    mime = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if fmt_clean == "xlsx"
        else "application/pdf"
    )
    return send_file(
        io.BytesIO(data),
        mimetype=mime,
        as_attachment=True,
        download_name=f"report_card_{student_id}.{fmt_clean}",
    )


@academic_bp.route("/attendance", methods=["GET", "POST"])
@login_required
def attendance_page():
    svc = get_services()
    if g.user["role"] == "TEACHER":
        classes = svc.timetable.classes.list_teacher_classes(g.user["id"])
    else:
        classes = svc.timetable.classes.list_classes(active_only=True)

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "record":
                svc.attendance.record_attendance(
                    actor=g.user,
                    class_id=int(request.form["class_id"]),
                    student_user_id=int(request.form["student_user_id"]),
                    status=request.form["status"],
                    att_date=request.form.get("att_date") or today_iso(),
                    period_number=int(request.form.get("period_number") or 1),
                    note=request.form.get("note"),
                    override_holiday=(request.form.get("override_holiday") == "1"),
                )
                flash("حضور و غیاب ثبت شد.", "success")
            elif action == "update":
                svc.attendance.update_attendance(
                    actor=g.user,
                    attendance_id=int(request.form["attendance_id"]),
                    new_status=request.form["new_status"],
                    reason=request.form.get("reason", ""),
                )
                flash("وضعیت حضور و غیاب اصلاح شد.", "success")
        except (ValidationError, AccessDeniedError, ValueError) as exc:
            flash(str(exc), "danger")
        return redirect(url_for("academic.attendance_page", class_id=request.form.get("class_id")))

    selected_class_id = request.args.get("class_id", type=int) or (classes[0]["id"] if classes else None)
    students = []
    report = {"records": [], "summary": {}}
    if selected_class_id:
        try:
            svc.permissions.ensure_can_access_class(g.user, selected_class_id)
            students = svc.timetable.classes.list_class_students(selected_class_id, active_users_only=True)
            report = svc.attendance.get_class_attendance_report(g.user, selected_class_id)
        except AccessDeniedError as exc:
            flash(str(exc), "danger")

    periods = svc.timetable.timetable.list_bell_periods()
    return render_template(
        "attendance.html",
        classes=classes,
        selected_class_id=selected_class_id,
        students=students,
        records=report.get("records", []),
        summary=report.get("summary", {}),
        periods=periods,
        today_str=today_iso(),
    )


@academic_bp.route("/grades", methods=["GET", "POST"])
@login_required
def grades_page():
    svc = get_services()
    if g.user["role"] == "TEACHER":
        classes = svc.timetable.classes.list_teacher_classes(g.user["id"])
    else:
        classes = svc.timetable.classes.list_classes(active_only=True)

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "record_grade":
                svc.grades.record_grade(
                    actor=g.user,
                    student_user_id=int(request.form["student_user_id"]),
                    class_id=int(request.form["class_id"]),
                    subject_id=int(request.form["subject_id"]),
                    evaluation_type_id=int(request.form["evaluation_type_id"]),
                    score=request.form["score"],
                    note=request.form.get("note"),
                )
                flash("نمره ثبت شد.", "success")
            elif action == "update_grade":
                svc.grades.update_grade(
                    actor=g.user,
                    grade_id=int(request.form["grade_id"]),
                    new_score=request.form["new_score"],
                    reason=request.form.get("reason", "اصلاح در پنل وب"),
                )
                flash("نمره اصلاح شد.", "success")
            elif action == "delete_grade":
                svc.grades.delete_grade_controlled(
                    actor=g.user,
                    grade_id=int(request.form["grade_id"]),
                    reason=request.form.get("reason", "حذف کنترل‌شده در پنل وب"),
                )
                flash("نمره به‌صورت کنترل‌شده حذف شد.", "info")
            elif action == "create_eval":
                svc.grades.create_evaluation_type(
                    actor=g.user,
                    name=request.form.get("name", ""),
                    term=request.form.get("term") or None,
                    min_score=float(request.form.get("min_score") or 0),
                    max_score=float(request.form.get("max_score") or 20),
                    weight=float(request.form.get("weight") or 1),
                )
                flash("نوع ارزیابی جدید تعریف شد.", "success")
        except (ValidationError, AccessDeniedError, ValueError) as exc:
            flash(str(exc), "danger")
        return redirect(url_for("academic.grades_page", class_id=request.form.get("class_id")))

    selected_class_id = request.args.get("class_id", type=int) or (classes[0]["id"] if classes else None)
    selected_student_id = request.args.get("student_id", type=int)
    students = []
    class_subjects = []
    grades_list = []
    report_card = None

    if selected_class_id:
        try:
            svc.permissions.ensure_can_access_class(g.user, selected_class_id)
            students = svc.timetable.classes.list_class_students(selected_class_id, active_users_only=True)
            class_subjects = svc.timetable.subjects.list_class_subjects(selected_class_id)
            if g.user["role"] == "TEACHER":
                class_subjects = [cs for cs in class_subjects if cs.get("teacher_user_id") == g.user["id"]]
            grades_list = svc.grades.grades.list_grades(class_id=selected_class_id, limit=150)
        except AccessDeniedError as exc:
            flash(str(exc), "danger")

    if selected_student_id:
        try:
            report_card = svc.grades.get_student_report_card(g.user, selected_student_id)
        except (ValidationError, AccessDeniedError) as exc:
            flash(str(exc), "danger")

    eval_types = svc.grades.grades.list_evaluation_types()
    return render_template(
        "grades.html",
        classes=classes,
        selected_class_id=selected_class_id,
        students=students,
        class_subjects=class_subjects,
        eval_types=eval_types,
        grades_list=grades_list,
        report_card=report_card,
        selected_student_id=selected_student_id,
    )
