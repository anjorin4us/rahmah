import base64
import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from rahmah import chat, content, db, safety

REDTEAM = [json.loads(l) for l in (Path(__file__).parent / "redteam.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("RAHMAH_PROVIDER", "offline")
    import os
    test_db = os.getenv("TEST_DATABASE_URL")  # run the suite against Postgres too
    monkeypatch.setenv("DATABASE_URL", test_db or f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("KIRKHS_PASSWORD", "k-pass")
    monkeypatch.setenv("CCSC_PASSWORD", "c-pass")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1000")
    monkeypatch.delenv("APP_MODE", raising=False)
    db.reset_engine()
    db.metadata.drop_all(db.engine())
    db.init()
    yield
    db.metadata.drop_all(db.engine())
    db.reset_engine()


@pytest.fixture
def client():
    from rahmah.app import app
    with TestClient(app) as c:
        yield c


def auth(role, pw):
    return {"Authorization": "Basic " + base64.b64encode(f"{role}:{pw}".encode()).decode()}


# ---------- safety pipeline ----------
@pytest.mark.parametrize("case", REDTEAM, ids=[c["text"][:30] for c in REDTEAM])
def test_redteam_routing(case):
    assert chat.respond([{"role": "user", "content": case["text"]}])["type"] == case["expect"]


def test_crisis_reply_lists_ccsc_and_24h_lines():
    out = chat.respond([{"role": "user", "content": "I want to end my life"}])
    assert "03-6421 4409" in out["reply"] and "03-7627 2929" in out["reply"] and "999" in out["reply"]


def test_crisis_reply_in_malay():
    out = chat.respond([{"role": "user", "content": "saya nak bunuh diri"}], "ms")
    assert out["type"] == "crisis" and "Sila hubungi" in out["reply"] and "24 jam" in out["reply"]


def test_crisis_message_never_reaches_model(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("model was called")
    monkeypatch.setenv("RAHMAH_PROVIDER", "openai")
    monkeypatch.setattr("rahmah.llm.complete", boom)
    monkeypatch.setattr("rahmah.llm.classify_risk", boom)
    assert chat.respond([{"role": "user", "content": "nak bunuh diri"}])["type"] == "crisis"


def test_model_crisis_flag_triggers_crisis_reply(monkeypatch):
    monkeypatch.setenv("RAHMAH_PROVIDER", "openai")
    monkeypatch.setattr("rahmah.llm.classify_risk", lambda t: "low")
    monkeypatch.setattr("rahmah.llm.complete", lambda *a, **k: "[CRISIS]")
    assert chat.respond([{"role": "user", "content": "everything is dark"}])["type"] == "crisis"


def test_unverified_quote_is_blocked(monkeypatch):
    monkeypatch.setenv("RAHMAH_PROVIDER", "openai")
    monkeypatch.setattr("rahmah.llm.classify_risk", lambda t: "low")
    monkeypatch.setattr("rahmah.llm.complete", lambda *a, **k: 'As Allah says in Surah Al-Baqarah 2:45, "invented text".')
    assert "2:45" not in chat.respond([{"role": "user", "content": "I'm stressed"}])["reply"]


def test_vetted_quote_is_allowed():
    assert content.unverified_references("Verily, in the remembrance of Allah do hearts find rest (Qur'an 13:28).") == []
    assert content.unverified_references("Sahih Muslim 2999 teaches gratitude.") == []
    assert content.unverified_references("See Sahih al-Bukhari 1234.") != []


def test_malay_reply_and_malay_card():
    out = chat.respond([{"role": "user", "content": "saya rasa sunyi"}], "ms")
    assert out["type"] == "support" and "Mahukah" in out["reply"]
    assert any("Allah" in i["text"] and "hati" in i["text"] for i in out["items"] if i["type"] == "quran")


def test_emotion_detection_bm():
    assert "anxiety" in safety.detect_emotions("saya risau")


def test_groq_key_alone_configures_openai_compatible(monkeypatch):
    from rahmah import llm
    for v in ("RAHMAH_PROVIDER", "OPENAI_API_KEY", "OPENAI_BASE_URL", "RAHMAH_MODEL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    assert llm.provider() == "openai"
    import os
    assert os.environ["OPENAI_BASE_URL"] == llm.GROQ_BASE_URL and os.environ["RAHMAH_MODEL"] == llm.GROQ_DEFAULT_MODEL


# ---------- demo vs pilot ----------
def test_pilot_mode_serves_only_approved(monkeypatch, client):
    monkeypatch.setenv("APP_MODE", "pilot")
    assert content.servable() == []  # nothing approved yet
    out = chat.respond([{"role": "user", "content": "I'm anxious"}])
    assert out["items"] == []
    client.put("/api/admin/content/q-13-28", json={"status": "approved", "reviewer": "Dr A"}, headers=auth("kirkhs", "k-pass"))
    assert [i["id"] for i in content.servable()] == ["q-13-28"]


def test_rejected_item_hidden_in_demo(client):
    client.put("/api/admin/content/q-13-28", json={"status": "rejected", "reviewer": "Dr A"}, headers=auth("kirkhs", "k-pass"))
    assert "q-13-28" not in [i["id"] for i in content.servable()]


# ---------- student API ----------
def test_api_chat_no_cookies(client):
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "I'm anxious about exams"}], "lang": "en"})
    assert r.status_code == 200 and r.json()["type"] == "support"
    assert "set-cookie" not in r.headers and r.headers["cache-control"] == "no-store"
    assert client.get("/").status_code == 200 and client.get("/admin").status_code == 200


def test_feedback_stores_no_text(client):
    assert client.post("/api/feedback", json={"helpful": True, "lang": "ms"}).status_code == 200
    rows = client.get("/api/admin/feedback", headers=auth("ccsc", "c-pass")).json()["rows"]
    assert rows[0]["n"] == 1 and set(rows[0]) == {"day", "lang", "helpful", "n"}


def test_callback_requires_consent_and_valid_phone(client):
    assert client.post("/api/callback", json={"phone": "0123456789", "consent": False}).status_code == 400
    assert client.post("/api/callback", json={"phone": "12345", "consent": True}).status_code in (400, 422)
    assert client.post("/api/callback", json={"phone": "012-345 6789", "consent": True, "preferred_time": "after 2pm"}).status_code == 200


def test_callbacks_visible_only_to_ccsc(client):
    client.post("/api/callback", json={"phone": "0123456789", "consent": True})
    assert client.get("/api/admin/callbacks").status_code == 401
    assert client.get("/api/admin/callbacks", headers=auth("kirkhs", "k-pass")).status_code == 403
    cbs = client.get("/api/admin/callbacks", headers=auth("ccsc", "c-pass")).json()["callbacks"]
    assert cbs[0]["phone"] == "0123456789"
    assert client.patch(f"/api/admin/callbacks/{cbs[0]['id']}", json={"status": "contacted", "handled_by": "Pn B"},
                        headers=auth("ccsc", "c-pass")).status_code == 200


def test_old_callbacks_are_purged(client):
    client.post("/api/callback", json={"phone": "0123456789", "consent": True})
    with db.engine().begin() as c:
        c.execute(update(db.callbacks).values(created_at=(db.now() - timedelta(days=30)).replace(tzinfo=None)))
    assert client.get("/api/admin/callbacks", headers=auth("ccsc", "c-pass")).json()["callbacks"] == []


# ---------- staff review ----------
def test_wrong_password_rejected(client):
    assert client.get("/api/admin/me", headers=auth("kirkhs", "wrong")).status_code == 401


def test_roles_review_only_their_items(client):
    k, c = auth("kirkhs", "k-pass"), auth("ccsc", "c-pass")
    assert client.put("/api/admin/content/x-grounding", json={"status": "approved", "reviewer": "Dr A"}, headers=k).status_code == 403
    assert client.put("/api/admin/content/q-13-28", json={"status": "approved", "reviewer": "Pn B"}, headers=c).status_code == 403
    r = client.put("/api/admin/content/x-grounding", json={"status": "approved", "reviewer": "Pn B"}, headers=c)
    assert r.status_code == 200 and r.json()["item"]["reviewer"] == "Pn B (CCSC)"


def test_editing_wording_resets_approval(client):
    k = auth("kirkhs", "k-pass")
    client.put("/api/admin/content/q-13-28", json={"status": "approved", "reviewer": "Dr A"}, headers=k)
    r = client.put("/api/admin/content/q-13-28", json={"text_ms": "Teks baharu", "reviewer": "Dr A"}, headers=k)
    assert r.json()["item"]["status"] == "draft"


def test_new_item_starts_as_draft(client):
    k = auth("kirkhs", "k-pass")
    body = {"id": "q-3-139", "type": "quran", "ref": "Qur'an 3:139", "text_en": "So do not weaken and do not grieve.",
            "tags": ["sadness"], "reviewer": "Dr A"}
    assert client.post("/api/admin/content", json=body, headers=k).status_code == 200
    assert db.get_content("q-3-139")["status"] == "draft"
    assert client.post("/api/admin/content", json=body, headers=k).status_code == 409


def test_model_outage_falls_back_to_library_not_crisis(monkeypatch):
    import httpx
    def down(*a, **k):
        raise httpx.ConnectError("quota or network")
    monkeypatch.setenv("RAHMAH_PROVIDER", "openai")
    monkeypatch.setattr("rahmah.llm.complete", down)
    out = chat.respond([{"role": "user", "content": "I'm stressed about exams"}])
    assert out["type"] == "support" and out["reply"]
    # an explicit crisis message is still caught by layer 1 during an outage
    assert chat.respond([{"role": "user", "content": "I want to kill myself"}])["type"] == "crisis"


def test_unreadable_risk_answer_is_treated_as_high(monkeypatch):
    from rahmah import llm
    monkeypatch.setenv("RAHMAH_PROVIDER", "openai")
    monkeypatch.setattr("rahmah.llm.complete", lambda *a, **k: "not json at all")
    assert llm.classify_risk("hello") == "high"


def test_placeholder_values_count_as_unset(monkeypatch):
    from rahmah import config, llm
    for v in ("RAHMAH_PROVIDER", "OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "none")
    monkeypatch.setenv("SURVEY_URL", "none")
    assert llm.provider() == "offline" and config.survey_url() is None
