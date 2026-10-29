"""Application settings, read from environment variables (or a local .env file).

Nothing secret is hard-coded here. See .env.example for every option.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # optional: load a local .env during development
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    timezone: str
    demo_mode: bool
    app_base_url: str
    secret_key: str
    smtp_host: str | None
    smtp_port: int
    smtp_user: str | None
    smtp_password: str | None
    smtp_from: str | None
    smtp_starttls: bool
    brand_logo_path: str | None
    org_name: str
    unit_name: str

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)


def _normalize_db_url(url: str) -> str:
    """Accept the plain 'postgres://' / 'postgresql://' URLs that Neon and Supabase hand out."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def get_settings() -> Settings:
    default_db = f"sqlite:///{PROJECT_ROOT / 'data' / 'scheduler.db'}"
    return Settings(
        database_url=_normalize_db_url(os.getenv("DATABASE_URL", default_db)),
        timezone=os.getenv("APP_TIMEZONE", "America/Chicago"),
        demo_mode=_bool("DEMO_MODE", True),
        app_base_url=os.getenv("APP_BASE_URL", "http://localhost:8501").rstrip("/"),
        secret_key=os.getenv("SECRET_KEY", "dev-only-change-me"),
        smtp_host=os.getenv("SMTP_HOST") or None,
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_user=os.getenv("SMTP_USER") or None,
        smtp_password=os.getenv("SMTP_PASSWORD") or None,
        smtp_from=os.getenv("SMTP_FROM") or None,
        smtp_starttls=_bool("SMTP_STARTTLS", True),
        brand_logo_path=os.getenv("BRAND_LOGO_PATH") or None,
        org_name=os.getenv("ORG_NAME", "University of North Dakota"),
        unit_name=os.getenv("UNIT_NAME", "Department of Communication"),
    )
