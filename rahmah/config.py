"""All settings come from environment variables, so the same code runs as a demo or a pilot."""
from __future__ import annotations

import os


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def mode() -> str:
    """'demo' (default): all non-rejected draft content is shown and a demo banner appears.
    'pilot': only content approved by reviewers is served; meant for real students after ethics approval."""
    return "pilot" if os.getenv("APP_MODE", "demo").lower() == "pilot" else "demo"


def database_url() -> str:
    url = (os.getenv("DATABASE_URL") or "sqlite:///./rahmah.db").strip()
    # Tolerate a string copied from Neon's "psql" tab, e.g.  psql 'postgresql://...'
    if url.startswith("psql "):
        url = url[5:].strip()
    url = url.strip("'\"")
    # Render/Neon give postgres:// or postgresql:// URLs; SQLAlchemy + psycopg needs postgresql+psycopg://
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


BLANK = {"", "none", "-", "n/a", "na"}


def env(name: str) -> str | None:
    """Read a setting, treating placeholder values such as 'none' as not set."""
    v = os.getenv(name)
    return None if v is None or v.strip().lower() in BLANK else v.strip()


def survey_url() -> str | None:
    v = env("SURVEY_URL")
    return v if v and v.startswith(("http://", "https://")) else None


def callback_enabled() -> bool:
    return _bool("CALLBACK_ENABLED", True)


def callback_retention_days() -> int:
    return int(os.getenv("CALLBACK_RETENTION_DAYS", "14"))


def reviewer_passwords() -> dict[str, str]:
    """Role -> password. A role with no password set cannot log in."""
    out = {}
    for role, var in (("kirkhs", "KIRKHS_PASSWORD"), ("ccsc", "CCSC_PASSWORD")):
        if os.getenv(var):
            out[role] = os.environ[var]
    return out


def rate_limit_per_minute() -> int:
    return int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))
