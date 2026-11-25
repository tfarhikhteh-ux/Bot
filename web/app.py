from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from flask import Flask, render_template

from config import AppConfig
from services import ServiceContainer
from utils.dates import format_jalali_date, format_jalali_datetime
from utils.formatters import attendance_label, holiday_type_label, role_label, week_type_label
from utils.security import RateLimiter

from .auth import auth_bp, ensure_csrf_token, init_auth_hooks
from .routes import academic_bp, dashboard_bp, operations_bp


def create_web_app(config: AppConfig, services: ServiceContainer) -> Flask:
    config.validate_web_settings()
    web_dir = Path(__file__).resolve().parent

    app = Flask(
        __name__,
        template_folder=str(web_dir / "templates"),
        static_folder=str(web_dir / "static"),
    )
    app.secret_key = config.web_secret_key
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.web_cookie_secure,
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=config.web_session_minutes),
        WEB_SESSION_MINUTES=config.web_session_minutes,
        WEB_MAX_LOGIN_ATTEMPTS=config.web_max_login_attempts,
        WEB_LOCKOUT_MINUTES=config.web_lockout_minutes,
        SERVICES=services,
        LOGIN_RATE_LIMITER=RateLimiter(max_requests=10, window_seconds=60),
    )

    # فیلترها و توابع کمکی فارسی در قالب‌های Jinja2
    app.jinja_env.filters["jdate"] = format_jalali_date
    app.jinja_env.filters["jdatetime"] = format_jalali_datetime
    app.jinja_env.filters["role_fa"] = role_label
    app.jinja_env.filters["att_fa"] = attendance_label
    app.jinja_env.filters["week_fa"] = week_type_label
    app.jinja_env.filters["holiday_fa"] = holiday_type_label
    app.jinja_env.globals["csrf_token"] = ensure_csrf_token

    init_auth_hooks(app)

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    @app.errorhandler(400)
    def bad_request_error(err):
        return render_template("error.html", code=400, message=getattr(err, "description", "درخواست نامعتبر است.")), 400

    @app.errorhandler(403)
    def forbidden_error(err):
        return render_template("error.html", code=403, message=getattr(err, "description", "دسترسی به این بخش مجاز نیست.")), 403

    @app.errorhandler(404)
    def not_found_error(_err):
        return render_template("error.html", code=404, message="صفحه موردنظر یافت نشد."), 404

    @app.errorhandler(500)
    def internal_error(_err):
        return render_template("error.html", code=500, message="خطای داخلی سرور رخ داد. جزئیات در گزارش سامانه ثبت شد."), 500

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(academic_bp)
    app.register_blueprint(operations_bp)
    return app
