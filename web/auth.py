from __future__ import annotations

from datetime import timedelta
from functools import wraps
from typing import Callable

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from services import AccessDeniedError, ServiceContainer
from utils.dates import now_tehran
from utils.security import generate_csrf_token, verify_csrf_token

auth_bp = Blueprint("auth", __name__)


def get_services() -> ServiceContainer:
    return current_app.config["SERVICES"]


def ensure_csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = generate_csrf_token()
    return session["csrf_token"]


def init_auth_hooks(app) -> None:
    @app.before_request
    def load_logged_in_user() -> None:
        g.user = None
        g.permissions = set()
        ensure_csrf_token()

        uid = session.get("user_id")
        last_seen = session.get("last_seen_ts")
        max_idle_sec = int(app.config.get("WEB_SESSION_MINUTES", 60)) * 60
        now_ts = int(now_tehran().timestamp())

        if uid is not None:
            if last_seen and (now_ts - int(last_seen)) > max_idle_sec:
                session.clear()
                ensure_csrf_token()
            else:
                svc = get_services()
                user = svc.registration.users.get_by_id(int(uid))
                if user and user.get("is_active"):
                    g.user = user
                    g.permissions = svc.permissions.get_effective_permissions(user)
                    session["last_seen_ts"] = now_ts
                else:
                    session.clear()
                    ensure_csrf_token()

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            provided = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if not verify_csrf_token(session.get("csrf_token"), provided):
                abort(400, description="توکن امنیتی فرم (CSRF) نامعتبر یا منقضی است.")


def login_required(view_fn: Callable):
    @wraps(view_fn)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            return redirect(url_for("auth.login"))
        return view_fn(*args, **kwargs)

    return wrapped


def permission_required(permission: str):
    def decorator(view_fn: Callable):
        @wraps(view_fn)
        @login_required
        def wrapped(*args, **kwargs):
            svc = get_services()
            try:
                svc.permissions.require_permission(
                    g.user,
                    permission,
                    entity_type="WEB_ROUTE",
                    entity_id=request.path,
                    ip_address=request.remote_addr,
                )
            except AccessDeniedError as exc:
                abort(403, description=str(exc))
            return view_fn(*args, **kwargs)

        return wrapped

    return decorator


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user") is not None:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        ip = request.remote_addr or "unknown"
        login_limiter = current_app.config["LOGIN_RATE_LIMITER"]
        if not login_limiter.allow(f"web_login:{ip}"):
            flash("تعداد تلاش‌های ورود بیش از حد مجاز است. چند دقیقه دیگر تلاش کنید.", "danger")
            return render_template("login.html"), 429

        raw_bale_id = (request.form.get("bale_user_id") or "").strip()
        password = request.form.get("password") or ""
        if not raw_bale_id.isdigit() or not password:
            flash("لطفاً شناسه عددی بله و رمز عبور را به درستی وارد کنید.", "danger")
            return render_template("login.html"), 400

        svc = get_services()
        try:
            user = svc.registration.authenticate_web_user(
                bale_user_id=int(raw_bale_id),
                password=password,
                max_attempts=int(current_app.config.get("WEB_MAX_LOGIN_ATTEMPTS", 5)),
                lockout_minutes=int(current_app.config.get("WEB_LOCKOUT_MINUTES", 15)),
                ip_address=ip,
            )
        except AccessDeniedError as exc:
            flash(str(exc), "danger")
            return render_template("login.html"), 401

        session.clear()
        session.permanent = True
        session["user_id"] = user["id"]
        session["last_seen_ts"] = int(now_tehran().timestamp())
        session["csrf_token"] = generate_csrf_token()
        flash(f"خوش آمدید، {user['full_name']}!", "success")
        return redirect(url_for("dashboard.index"))

    return render_template("login.html")


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    svc = get_services()
    svc.audit.log(
        action="WEB_LOGOUT",
        entity_type="AUTH",
        actor=g.user,
        ip_address=request.remote_addr,
        description="خروج امن از پنل وب",
    )
    session.clear()
    flash("شما با موفقیت از سامانه خارج شدید.", "info")
    return redirect(url_for("auth.login"))
