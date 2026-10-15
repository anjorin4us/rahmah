"""Vetted content library (stored in the database), retrieval, and the quotation check.

Demo mode serves every item that is not rejected. Pilot mode serves ONLY items a reviewer approved.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config, db

ROOT = Path(__file__).resolve().parent.parent
RESOURCES = json.loads((ROOT / "content" / "resources.json").read_text(encoding="utf-8"))["resources"]


def servable() -> list[dict]:
    items = db.list_content()
    if config.mode() == "pilot":
        return [i for i in items if i["status"] == "approved"]
    return [i for i in items if i["status"] != "rejected"]


def localise(item: dict, lang: str) -> dict:
    """The fields the student sees, in their language (falls back to English if no translation)."""
    text = item.get("text_ms") if lang == "ms" and item.get("text_ms") else item["text_en"]
    return {"id": item["id"], "type": item["type"], "ref": item["ref"], "arabic": item.get("arabic"), "text": text,
            "status": item["status"]}


def retrieve(emotions: list[str], lang: str = "en", k_spiritual: int = 1, k_exercise: int = 1) -> list[dict]:
    items = servable()

    def score(item: dict) -> int:
        hits = sum(1 for e in emotions if e in item["tags"])
        return hits * 2 if hits else (1 if "general" in item["tags"] else 0)

    spiritual = sorted([i for i in items if i["type"] in ("quran", "dua", "hadith")], key=score, reverse=True)
    exercises = sorted([i for i in items if i["type"] == "exercise"], key=score, reverse=True)
    picked = [i for i in spiritual[:k_spiritual] if score(i) > 0] + [i for i in exercises[:k_exercise] if score(i) > 0]
    return [localise(i, lang) for i in picked]


def resources(lang: str = "en") -> list[dict]:
    return [{"name": r.get("name_ms", r["name"]) if lang == "ms" else r["name"],
             "contact": r["contact"],
             "hours": r.get("hours_ms", r["hours"]) if lang == "ms" else r["hours"]} for r in RESOURCES]


# ---- Output check: block religious references that are not in the served library ----

_REF_RX = re.compile(r"(?:qur'?an|quran|surah|surat|al-)\s*[\w\-' ]{0,20}?(\d{1,3})\s*[:.]\s*(\d{1,3})", re.I)
_BARE_REF_RX = re.compile(r"\((\d{1,3}):(\d{1,3})(?:-\d{1,3})?\)")
_HADITH_RX = re.compile(r"(bukhari|muslim|tirmidhi|abu dawud|nasa'?i|ibn majah)\s*(?:no\.?\s*)?(\d{1,5})?", re.I)


def _allowed_refs() -> tuple[set[tuple[int, int]], set[str]]:
    verses, hadith = set(), set()
    for i in servable():
        for m in re.finditer(r"(\d{1,3}):(\d{1,3})(?:-(\d{1,3}))?", i["ref"]):
            s, a, b = int(m.group(1)), int(m.group(2)), int(m.group(3) or m.group(2))
            verses.update((s, v) for v in range(a, b + 1))
        hm = _HADITH_RX.search(i["ref"])
        if hm and hm.group(2):
            hadith.add(f"{hm.group(1).lower()} {hm.group(2)}")
    return verses, hadith


def unverified_references(reply: str) -> list[str]:
    """Return any Qur'an or hadith references in `reply` that are not in the served library."""
    verses, hadith = _allowed_refs()
    bad = []
    for rx in (_REF_RX, _BARE_REF_RX):
        for m in rx.finditer(reply):
            if (int(m.group(1)), int(m.group(2))) not in verses:
                bad.append(m.group(0))
    for m in _HADITH_RX.finditer(reply):
        if m.group(2) and f"{m.group(1).lower()} {m.group(2)}" not in hadith:
            bad.append(m.group(0))
    return bad
