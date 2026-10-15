"""FastAPI server.

Student side: anonymous chat, anonymous feedback, opt-in callback request. No accounts, no cookies,
no message logging. Staff side (/admin): content review for KIRKHS and CCSC, callback list for CCSC,
feedback counts. Staff log in with a role password set in the environment.
"""
from __future__ import annotations

import re
import secrets
import time
from contextlib import asynccontextmanager
from collections import defaultdict, deque
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import chat, config, content, db, llm

STATIC = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_app):
    db.init()
    yield


app = FastAPI(title="RAHMAH", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


# ---------------- simple in-memory rate limit (IP kept in memory only, never stored) ----------------
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(request: Request):
    key = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]
    now = time.monotonic()
    q = _hits[key]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= config.rate_limit_per_minute():
        raise HTTPException(429, "Too many requests. Please wait a moment.")
    q.append(now)


@app.middleware("http")
async def headers(request, call_next):
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp


# ---------------- student API ----------------
Lang = Literal["en", "ms"]


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=60)
    lang: Lang = "en"


@app.post("/api/chat", dependencies=[Depends(rate_limit)])
def chat_endpoint(req: ChatRequest) -> dict:
    history = [m.model_dump() for m in req.messages]
    if history[-1]["role"] != "user":
        raise HTTPException(400, "The last message must come from the user.")
    return chat.respond(history, req.lang)


@app.get("/api/config")
def public_config(lang: Lang = "en") -> dict:
    return {"mode": config.mode(), "survey_url": config.survey_url(),
            "callback_enabled": config.callback_enabled(), "retention_days": config.callback_retention_days(),
            "resources": content.resources(lang)}


class Feedback(BaseModel):
    helpful: bool
    reply_type: Literal["support", "crisis"] = "support"
    lang: Lang = "en"


@app.post("/api/feedback", dependencies=[Depends(rate_limit)])
def feedback_endpoint(f: Feedback) -> dict:
    db.add_feedback(f.lang, f.reply_type, f.helpful)
    return {"ok": True}


MY_PHONE = re.compile(r"^(\+?60|0)1\d{8,9}$")  # Malaysian mobile numbers


class CallbackRequest(BaseModel):
    phone: str = Field(min_length=9, max_length=20)
    preferred_time: str | None = Field(default=None, max_length=40)
    consent: bool
    lang: Lang = "en"


@app.post("/api/callback", dependencies=[Depends(rate_limit)])
def callback_endpoint(r: CallbackRequest) -> dict:
    if not config.callback_enabled():
        raise HTTPException(404, "Callback requests are not enabled.")
    if not r.consent:
        raise HTTPException(400, "Consent is required.")
    phone = re.sub(r"[\s\-()]", "", r.phone)
    if not MY_PHONE.match(phone):
        raise HTTPException(400, "Please enter a Malaysian mobile number, for example 012-345 6789.")
    db.add_callback(phone, (r.preferred_time or "").strip() or None, r.lang)
    return {"ok": True, "retention_days": config.callback_retention_days()}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "mode": config.mode(), "provider": llm.provider()}


# ---------------- staff API ----------------
security = HTTPBasic(auto_error=False)
RELIGIOUS = {"quran", "dua", "hadith"}


def staff(creds: HTTPBasicCredentials | None = Depends(security)) -> str:
    pw = config.reviewer_passwords()
    if creds and creds.username in pw and secrets.compare_digest(creds.password.encode(), pw[creds.username].encode()):
        return creds.username
    raise HTTPException(401, "Staff login required.")


def may_review(role: str, item_type: str) -> bool:
    """KIRKHS approves religious items; CCSC approves exercises."""
    return (role == "kirkhs" and item_type in RELIGIOUS) or (role == "ccsc" and item_type == "exercise")


@app.get("/api/admin/me")
def me(role: str = Depends(staff)) -> dict:
    return {"role": role, "mode": config.mode()}


@app.get("/api/admin/content")
def admin_content(role: str = Depends(staff)) -> dict:
    items = db.list_content()
    for i in items:
        i["can_review"] = may_review(role, i["type"])
    return {"items": items}


class ContentEdit(BaseModel):
    ref: str | None = Field(default=None, max_length=200)
    arabic: str | None = Field(default=None, max_length=2000)
    text_en: str | None = Field(default=None, max_length=2000)
    text_ms: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = None
    status: Literal["draft", "approved", "needs_changes", "rejected"] | None = None
    notes: str | None = Field(default=None, max_length=2000)
    reviewer: str = Field(min_length=2, max_length=120)


@app.put("/api/admin/content/{item_id}")
def admin_update(item_id: str, e: ContentEdit, role: str = Depends(staff)) -> dict:
    item = db.get_content(item_id)
    if not item:
        raise HTTPException(404, "Not found.")
    if not may_review(role, item["type"]):
        raise HTTPException(403, "This item is reviewed by the other team.")
    changes = {k: v for k, v in e.model_dump().items() if v is not None}
    changes["reviewer"] = f"{e.reviewer} ({role.upper()})"
    # Any edit to the wording sends an approved item back to review unless it is approved in the same step.
    if any(k in changes for k in ("ref", "arabic", "text_en", "text_ms")) and "status" not in changes:
        changes["status"] = "draft"
    changes["id"] = item_id
    db.upsert_content(changes, create=False)
    return {"ok": True, "item": db.get_content(item_id)}


class ContentNew(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9\-]{3,64}$")
    type: Literal["quran", "dua", "hadith", "exercise"]
    ref: str = Field(min_length=2, max_length=200)
    arabic: str | None = Field(default=None, max_length=2000)
    text_en: str = Field(min_length=2, max_length=2000)
    text_ms: str | None = Field(default=None, max_length=2000)
    tags: list[str] = Field(min_length=1)
    reviewer: str = Field(min_length=2, max_length=120)


@app.post("/api/admin/content")
def admin_create(n: ContentNew, role: str = Depends(staff)) -> dict:
    if not may_review(role, n.type):
        raise HTTPException(403, "This type of item is added by the other team.")
    if db.get_content(n.id):
        raise HTTPException(409, "An item with this id already exists.")
    item = n.model_dump()
    item["reviewer"] = f"{n.reviewer} ({role.upper()})"
    item["status"] = "draft"
    db.upsert_content(item, create=True)
    return {"ok": True}


@app.get("/api/admin/callbacks")
def admin_callbacks(role: str = Depends(staff)) -> dict:
    if role != "ccsc":
        raise HTTPException(403, "Only CCSC can see callback requests.")
    return {"callbacks": db.list_callbacks(), "retention_days": config.callback_retention_days()}


class CallbackUpdate(BaseModel):
    status: Literal["new", "contacted", "closed"]
    handled_by: str = Field(min_length=2, max_length=120)


@app.patch("/api/admin/callbacks/{cb_id}")
def admin_callback_update(cb_id: int, u: CallbackUpdate, role: str = Depends(staff)) -> dict:
    if role != "ccsc":
        raise HTTPException(403, "Only CCSC can update callback requests.")
    db.update_callback(cb_id, u.status, u.handled_by)
    return {"ok": True}


@app.get("/api/admin/feedback")
def admin_feedback(role: str = Depends(staff)) -> dict:
    return {"rows": db.feedback_summary()}


# ---------------- pages ----------------
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/admin")
def admin_page() -> FileResponse:
    return FileResponse(STATIC / "admin.html")
