"""Database: the reviewed content library, anonymous feedback counts and opt-in callback requests.

No table ever stores conversation text. Feedback stores only a yes/no, the language and the day.
Callback requests hold a phone number the student chose to give; they are deleted automatically
after CALLBACK_RETENTION_DAYS.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import (Boolean, Column, Date, DateTime, Integer, MetaData, String, Table, Text,
                        create_engine, delete, func, insert, select, update)

from . import config

ROOT = Path(__file__).resolve().parent.parent
metadata = MetaData()

content_items = Table(
    "content_items", metadata,
    Column("id", String(64), primary_key=True),
    Column("type", String(16), nullable=False),          # quran | dua | hadith | exercise
    Column("ref", String(200), nullable=False),
    Column("arabic", Text),
    Column("text_en", Text, nullable=False),
    Column("text_ms", Text),
    Column("tags", Text, nullable=False),                 # JSON list
    Column("status", String(16), nullable=False, default="draft"),  # draft | approved | needs_changes | rejected
    Column("reviewer", String(120)),
    Column("reviewed_at", DateTime(timezone=True)),
    Column("notes", Text),
    Column("updated_at", DateTime(timezone=True)),
)

feedback = Table(
    "feedback", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("day", Date, nullable=False),
    Column("lang", String(4), nullable=False),
    Column("reply_type", String(16), nullable=False),
    Column("helpful", Boolean, nullable=False),
)

callbacks = Table(
    "callbacks", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("phone", String(32), nullable=False),
    Column("preferred_time", String(40)),
    Column("lang", String(4), nullable=False),
    Column("status", String(16), nullable=False, default="new"),   # new | contacted | closed
    Column("handled_by", String(120)),
)

_engine = None


def engine():
    global _engine
    if _engine is None:
        url = config.database_url()
        kw = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
        _engine = create_engine(url, **kw)
    return _engine


def reset_engine():
    global _engine
    _engine = None


def now():
    return datetime.now(timezone.utc)


def init():
    """Create tables and seed the library from content/library.json on first run."""
    metadata.create_all(engine())
    with engine().begin() as c:
        if c.execute(select(func.count()).select_from(content_items)).scalar() == 0:
            items = json.loads((ROOT / "content" / "library.json").read_text(encoding="utf-8"))["items"]
            for i in items:
                c.execute(insert(content_items).values(
                    id=i["id"], type=i["type"], ref=i["ref"], arabic=i.get("arabic"),
                    text_en=i["text"], text_ms=i.get("text_ms"), tags=json.dumps(i["tags"]),
                    status=i["review"]["status"], reviewer=i["review"].get("reviewer"), updated_at=now()))
    purge_old_callbacks()


# ---------- content ----------
def _row(r) -> dict:
    d = dict(r._mapping)
    d["tags"] = json.loads(d["tags"])
    for k in ("reviewed_at", "updated_at"):
        if d.get(k):
            d[k] = d[k].isoformat()
    return d


def list_content() -> list[dict]:
    with engine().connect() as c:
        return [_row(r) for r in c.execute(select(content_items).order_by(content_items.c.type, content_items.c.id))]


def get_content(item_id: str) -> dict | None:
    with engine().connect() as c:
        r = c.execute(select(content_items).where(content_items.c.id == item_id)).first()
        return _row(r) if r else None


def upsert_content(item: dict, create: bool):
    vals = {k: item[k] for k in ("type", "ref", "arabic", "text_en", "text_ms", "status", "reviewer", "notes") if k in item}
    if "tags" in item:
        vals["tags"] = json.dumps(item["tags"])
    if item.get("status") in ("approved", "rejected", "needs_changes"):
        vals["reviewed_at"] = now()
    vals["updated_at"] = now()
    with engine().begin() as c:
        if create:
            c.execute(insert(content_items).values(id=item["id"], **vals))
        else:
            c.execute(update(content_items).where(content_items.c.id == item["id"]).values(**vals))


# ---------- feedback ----------
def add_feedback(lang: str, reply_type: str, helpful: bool):
    with engine().begin() as c:
        c.execute(insert(feedback).values(day=date.today(), lang=lang, reply_type=reply_type, helpful=helpful))


def feedback_summary() -> list[dict]:
    q = (select(feedback.c.day, feedback.c.lang, feedback.c.helpful, func.count().label("n"))
         .group_by(feedback.c.day, feedback.c.lang, feedback.c.helpful).order_by(feedback.c.day.desc()))
    with engine().connect() as c:
        return [{"day": r.day.isoformat(), "lang": r.lang, "helpful": r.helpful, "n": r.n} for r in c.execute(q)]


# ---------- callbacks ----------
def add_callback(phone: str, preferred_time: str | None, lang: str) -> int:
    with engine().begin() as c:
        res = c.execute(insert(callbacks).values(created_at=now(), phone=phone, preferred_time=preferred_time,
                                                 lang=lang, status="new"))
        return res.inserted_primary_key[0]


def list_callbacks() -> list[dict]:
    purge_old_callbacks()
    with engine().connect() as c:
        rows = c.execute(select(callbacks).order_by(callbacks.c.created_at.desc()))
        out = []
        for r in rows:
            d = dict(r._mapping)
            ts = d["created_at"]
            if ts.tzinfo is None:  # SQLite drops the timezone; values are stored in UTC
                ts = ts.replace(tzinfo=timezone.utc)
            d["created_at"] = ts.isoformat()
            out.append(d)
        return out


def update_callback(cb_id: int, status: str, handled_by: str):
    with engine().begin() as c:
        c.execute(update(callbacks).where(callbacks.c.id == cb_id).values(status=status, handled_by=handled_by))


def purge_old_callbacks():
    cutoff = now() - timedelta(days=config.callback_retention_days())
    if engine().dialect.name == "sqlite":
        cutoff = cutoff.replace(tzinfo=None)
    with engine().begin() as c:
        c.execute(delete(callbacks).where(callbacks.c.created_at < cutoff))
