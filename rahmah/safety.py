"""Safety layer 1: deterministic crisis screening.

Runs on every user message BEFORE the language model sees it. A match means
the message is never sent to the model; RAHMAH returns the fixed crisis reply.

The phrase list below is a starting point written by the developers. It MUST be
reviewed and extended with IIUM CCSC counsellors (English, Bahasa Melayu,
Arabic and common Manglish / code-switched forms) before any pilot.
"""
from __future__ import annotations

import re
import unicodedata

# Each pattern is matched against lower-cased, accent-stripped text.
CRISIS_PATTERNS: dict[str, list[str]] = {
    "en": [
        r"\bsuicid(e|al)\b",
        r"\bkill(ing)? my ?self\b",
        r"\bend (it all|my life|everything)\b",
        r"\btake my (own )?life\b",
        r"\b(want|wanna|going) to die\b",
        r"\bwish i (was|were) dead\b",
        r"\bbetter off (dead|without me)\b",
        r"\b(don'?t|do not|dont) want to (live|be alive|exist|wake up)\b",
        r"\bno (reason|point) (to|in) (live|living|go on)\b",
        r"\b(hurt|harm|cut|cutting|burn|burning) my ?self\b",
        r"\bself[- ]?harm(ing)?\b",
        r"\boverdos(e|ing)\b",
        r"\bjump (off|from) (a|the)\b",
        r"\bcan'?t go on\b",
    ],
    "ms": [
        r"\bbunuh diri\b",
        r"\bnak (mati|bunuh diri)\b",
        r"\bingin mati\b",
        r"\b(tak|tidak|x) (nak|mahu|ingin) hidup\b",
        r"\bhidup (ni |ini )?tak (ada |de )?(makna|guna)\b",
        r"\b(cederakan|sakitkan|toreh|kelar) (diri|tangan)\b",
        r"\bmati (lagi|je) (baik|bagus)\b",
        r"\bpenat hidup\b",
    ],
    "ar": [
        r"انتحار",
        r"انتحر",
        r"(اريد|أريد|بدي) (ان |أن )?اموت",
        r"(اقتل|أقتل) نفسي",
        r"(ايذاء|إيذاء) نفسي",
        r"لا (اريد|أريد) (ان |أن )?(اعيش|أعيش)",
    ],
}

_COMPILED = [(lang, re.compile(p)) for lang, ps in CRISIS_PATTERNS.items() for p in ps]


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("’", "'").replace("‘", "'")
    # Arabic: unify alef forms and drop diacritics so spelling variants match.
    text = re.sub("[أإآ]", "ا", text)
    text = re.sub("[ً-ْ]", "", text)
    return re.sub(r"\s+", " ", text)


def screen(text: str) -> dict:
    """Return {'crisis': bool, 'matches': [(lang, pattern)]}."""
    t = normalise(text)
    matches = [(lang, rx.pattern) for lang, rx in _COMPILED if rx.search(t)]
    return {"crisis": bool(matches), "matches": matches}


# Light emotion tagging used only to choose vetted content and tone.
EMOTION_KEYWORDS: dict[str, list[str]] = {
    "anxiety": ["anxious", "anxiety", "worried", "worry", "nervous", "panic", "scared", "afraid", "risau", "cemas", "takut", "bimbang"],
    "stress": ["stress", "stressed", "pressure", "tension", "tekanan", "serabut"],
    "academic": ["exam", "exams", "assignment", "deadline", "fyp", "cgpa", "gpa", "lecturer", "fail", "failed", "peperiksaan", "tugasan"],
    "sadness": ["sad", "down", "depressed", "crying", "cry", "empty", "sedih", "menangis", "kecewa"],
    "loneliness": ["lonely", "alone", "no friends", "isolated", "homesick", "sunyi", "keseorangan", "rindu"],
    "anger": ["angry", "furious", "annoyed", "frustrated", "marah", "geram"],
    "guilt": ["guilty", "guilt", "ashamed", "shame", "regret", "sin", "dosa", "bersalah", "malu"],
    "overwhelm": ["overwhelmed", "too much", "can't cope", "cannot cope", "drowning", "penat", "letih"],
}


def detect_emotions(text: str) -> list[str]:
    t = normalise(text)
    found = [emo for emo, words in EMOTION_KEYWORDS.items() if any(re.search(rf"\b{re.escape(w)}\b", t) for w in words)]
    return found or ["general"]
