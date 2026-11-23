"""Application configuration.

All secrets come from environment variables. Nothing secret is stored in code,
the database or release archives. See `.env.example` for the full list.
"""
from __future__ import annotations

import os
from datetime import timedelta


def _bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def normalise_database_url(url: str | None) -> str | None:
    """Render hands out `postgres://` URLs. Use the psycopg 3 driver explicitly."""
    if not url:
        return url
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


class BaseConfig:
    APP_NAME = "PORTIA SkillBridge"
    APP_TAGLINE = "Bridging Skills, Building Futures."
    APP_DEVELOPER = "Kayron Global Tech Services"
    APP_VERSION = "1.0.1"

    SECRET_KEY = os.environ.get("SECRET_KEY")

    SQLALCHEMY_DATABASE_URI = normalise_database_url(os.environ.get("DATABASE_URL")) or "sqlite:///portia_dev.db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Sessions & cookies
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    # CSRF
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600

    # Rate limiting (use a Redis URL in production with several workers)
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_DEFAULT = os.environ.get("RATELIMIT_DEFAULT", "300 per minute")
    RATELIMIT_HEADERS_ENABLED = True

    # Uploads (used from Stage 4 onwards)
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "10")) * 1024 * 1024
    STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")  # local | s3
    UPLOAD_ROOT = os.environ.get("UPLOAD_ROOT")  # local backend; default instance/uploads
    STORAGE_BUCKET = os.environ.get("STORAGE_BUCKET")
    STORAGE_ENDPOINT_URL = os.environ.get("STORAGE_ENDPOINT_URL")
    STORAGE_REGION = os.environ.get("STORAGE_REGION")
    STORAGE_ACCESS_KEY_ID = os.environ.get("STORAGE_ACCESS_KEY_ID")
    STORAGE_SECRET_ACCESS_KEY = os.environ.get("STORAGE_SECRET_ACCESS_KEY")

    # Billing
    DEFAULT_CURRENCY = "GHS"
    BILLING_GRACE_DAYS = int(os.environ.get("BILLING_GRACE_DAYS", "7"))
    PAYSTACK_PUBLIC_KEY = os.environ.get("PAYSTACK_PUBLIC_KEY")
    PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY")
    # live | fake. Unset: live when a secret key is set; otherwise fake (practice checkout) outside production
    # and online payment switched off in production.
    PAYSTACK_BACKEND = os.environ.get("PAYSTACK_BACKEND", "")

    # Email (password resets, later invitations and notifications)
    MAIL_BACKEND = os.environ.get("MAIL_BACKEND", "log")  # log | smtp | memory
    MAIL_FROM = os.environ.get("MAIL_FROM", "PORTIA SkillBridge <no-reply@localhost>")
    SMTP_HOST = os.environ.get("SMTP_HOST")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
    SMTP_USE_TLS = _bool("SMTP_USE_TLS", True)

    SALES_EMAIL = os.environ.get("SALES_EMAIL", "")
    # The site's public address, used for links in emails sent by scheduled jobs (no web request to copy it from).
    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "")
    SECURITY_CONTACT = os.environ.get("SECURITY_CONTACT", "")  # email for security reports (security.txt)
    SLOW_REQUEST_MS = int(os.environ.get("SLOW_REQUEST_MS", "1500"))
    # Backups: a Fernet key (make one with `flask backup make-key`). Keep a copy somewhere safe, off this server.
    BACKUP_ENCRYPTION_KEY = os.environ.get("BACKUP_ENCRYPTION_KEY")
    BACKUP_RETENTION_DAYS = int(os.environ.get("BACKUP_RETENTION_DAYS", "30"))

    # Sign-in throttling (per IP address, on top of the per-account lockout)
    LOGIN_RATE_LIMIT = os.environ.get("LOGIN_RATE_LIMIT", "10 per minute;50 per hour")
    SIGNUP_RATE_LIMIT = os.environ.get("SIGNUP_RATE_LIMIT", "5 per hour")
    RESET_RATE_LIMIT = os.environ.get("RESET_RATE_LIMIT", "5 per hour")

    SECURITY_HSTS = False


class DevelopmentConfig(BaseConfig):
    ENV_NAME = "development"
    DEBUG = True
    SECRET_KEY = BaseConfig.SECRET_KEY or "dev-only-insecure-key-change-me"


class TestingConfig(BaseConfig):
    ENV_NAME = "testing"
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = normalise_database_url(os.environ.get("TEST_DATABASE_URL")) or "sqlite://"
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = True
    RATELIMIT_STORAGE_URI = "memory://"
    MAIL_BACKEND = "memory"
    STORAGE_BACKEND = "local"
    PAYSTACK_BACKEND = "fake"
    PAYSTACK_SECRET_KEY = None
    UPLOAD_ROOT = None  # set per test run (temporary folder)


class ProductionConfig(BaseConfig):
    ENV_NAME = "production"
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    SEND_FILE_MAX_AGE_DEFAULT = 31_536_000  # static files carry ?v=<version>, so a year is safe
    PREFERRED_URL_SCHEME = "https"
    SECURITY_HSTS = True

    @classmethod
    def validate(cls) -> None:
        if not os.environ.get("SECRET_KEY") or len(os.environ["SECRET_KEY"]) < 32:
            raise RuntimeError("SECRET_KEY must be set to a random value of at least 32 characters in production.")
        if not os.environ.get("DATABASE_URL"):
            raise RuntimeError("DATABASE_URL must be set in production.")


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name: str | None = None):
    name = (name or os.environ.get("APP_ENV") or "development").lower()
    if name not in CONFIGS:
        raise ValueError(f"Unknown APP_ENV '{name}'. Use one of: {', '.join(CONFIGS)}")
    return CONFIGS[name]
