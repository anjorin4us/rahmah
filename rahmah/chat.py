"""The RAHMAH message pipeline (Figure 1 in the paper).

  student message
    -> layer 1: crisis screen (rules)           -> crisis reply (no AI)
    -> layer 2: model risk check                -> crisis reply (no AI)
    -> retrieve vetted content for the emotions
    -> language model with constrained prompt
    -> output check: unverified quotes blocked  -> safe fallback reply
    -> reply

Stateless: the browser sends the conversation with each request; the server never stores or logs it.
"""
from __future__ import annotations

from . import content, llm, safety

MAX_TURNS = 12  # only the most recent turns are sent to the model
LANG_NAME = {"en": "English", "ms": "Bahasa Melayu"}

SYSTEM_PROMPT = """You are RAHMAH, a warm, gentle wellbeing companion for students at the International Islamic University Malaysia (IIUM).

Your role
- Listen, reflect feelings back, and offer gentle support grounded in Islamic psychology and CBT-style self-help.
- You are NOT a therapist, doctor, counsellor or mufti. Never diagnose, never suggest or discuss medication, never issue religious rulings (fatwa). If asked for a ruling, suggest asking a qualified scholar; if asked for a diagnosis, suggest IIUM CCSC.
- Never discourage professional help. When distress seems ongoing or heavy, gently encourage contacting IIUM CCSC (03-6421 4409).

Style
- Short replies: 2-5 sentences, plain language, one idea at a time. Ask at most one open question.
- Validate first, then offer one small, practical step.
- Reply in {language}, unless the student clearly writes in another language; then match them.
- Offer spiritual content gently, never preach or imply that distress means weak faith.

Religious content (strict)
- You may quote the Qur'an, hadith or du'a ONLY from the VETTED CONTENT block below, word for word, with its reference.
- Never quote, paraphrase or cite any other verse, hadith, chapter number or hadith number. If nothing in the block fits, do not quote anything religious.
- The vetted content is also shown to the student as a card, so you may simply refer to it.

Safety
- If the student mentions suicide, self-harm, wanting to die, or being in danger, stop and reply only: "[CRISIS]".
"""

TEXT = {
    "en": {
        "crisis_intro": "I'm really glad you told me. What you're feeling matters, and you don't have to face it alone. "
                        "Please reach out to someone right now. These people are ready to listen:",
        "crisis_end": "If you are in immediate danger, call 999 or go to the nearest emergency department. "
                      "If you can, tell someone you trust who is near you.",
        "openers": {
            "anxiety": "It sounds like a lot of worry is sitting with you right now.",
            "stress": "That sounds like a lot of pressure to carry.",
            "academic": "Studies can feel really heavy, especially when everything piles up at once.",
            "sadness": "I'm sorry you're feeling this low. It's okay to feel sad.",
            "loneliness": "Feeling alone is hard, especially far from home or family.",
            "anger": "It makes sense to feel frustrated when things feel unfair.",
            "guilt": "Carrying guilt can be exhausting. Allah's mercy is wider than any mistake.",
            "overwhelm": "When everything feels like too much, it helps to take just one small step.",
            "general": "Thank you for sharing that with me. I'm here to listen.",
        },
        "exercise": "One small thing that may help: ",
        "card": "I've also shared a reminder below that many people find comforting.",
        "ask": "Would you like to tell me a little more about what's been happening?",
    },
    "ms": {
        "crisis_intro": "Terima kasih kerana memberitahu saya. Perasaan anda penting, dan anda tidak perlu menghadapinya seorang diri. "
                        "Sila hubungi seseorang sekarang. Mereka sedia mendengar:",
        "crisis_end": "Jika anda dalam bahaya segera, hubungi 999 atau pergi ke jabatan kecemasan terdekat. "
                      "Jika boleh, beritahu seseorang yang anda percayai yang berada berdekatan.",
        "openers": {
            "anxiety": "Nampaknya banyak kerisauan sedang membebani anda sekarang.",
            "stress": "Itu kedengaran seperti tekanan yang berat untuk ditanggung.",
            "academic": "Pelajaran boleh terasa sangat berat, terutamanya apabila semuanya bertimbun serentak.",
            "sadness": "Saya bersimpati anda berasa begitu sedih. Tidak mengapa untuk berasa sedih.",
            "loneliness": "Berasa keseorangan memang sukar, lebih-lebih lagi apabila jauh dari rumah atau keluarga.",
            "anger": "Wajar untuk berasa kecewa apabila sesuatu terasa tidak adil.",
            "guilt": "Menanggung rasa bersalah boleh meletihkan. Rahmat Allah lebih luas daripada sebarang kesilapan.",
            "overwhelm": "Apabila semuanya terasa terlalu banyak, ambil satu langkah kecil dahulu.",
            "general": "Terima kasih kerana berkongsi dengan saya. Saya di sini untuk mendengar.",
        },
        "exercise": "Satu perkara kecil yang mungkin membantu: ",
        "card": "Saya juga kongsikan satu peringatan di bawah yang ramai rasa menenangkan.",
        "ask": "Mahukah anda ceritakan sedikit lagi tentang apa yang berlaku?",
    },
}


def _lang(lang: str | None) -> str:
    return lang if lang in TEXT else "en"


def crisis_reply(lang: str = "en") -> dict:
    t = TEXT[_lang(lang)]
    res = content.resources(lang)
    lines = [f"- {r['name']}: {r['contact']} ({r['hours']})" for r in res]
    text = t["crisis_intro"] + "\n" + "\n".join(lines) + "\n" + t["crisis_end"]
    return {"type": "crisis", "reply": text, "resources": res, "items": []}


def _vetted_block(items: list[dict]) -> str:
    if not items:
        return "VETTED CONTENT: (none for this message)"
    return "VETTED CONTENT:\n" + "\n".join(f"- [{i['type']}] {i['text']} ({i['ref']})" for i in items)


def offline_reply(emotions: list[str], items: list[dict], lang: str = "en") -> str:
    t = TEXT[_lang(lang)]
    parts = [t["openers"].get(emotions[0], t["openers"]["general"])]
    for i in items:
        if i["type"] == "exercise":
            parts.append(t["exercise"] + i["text"])
    if any(i["type"] != "exercise" for i in items):
        parts.append(t["card"])
    parts.append(t["ask"])
    return " ".join(parts)


def respond(history: list[dict], lang: str = "en") -> dict:
    """history: [{'role': 'user'|'assistant', 'content': str}], last item is the new user message."""
    lang = _lang(lang)
    user_text = history[-1]["content"]

    # Layer 1: deterministic crisis screen, before any model call.
    if safety.screen(user_text)["crisis"]:
        return crisis_reply(lang)

    # Layer 2: model-based risk check (skipped offline).
    if llm.classify_risk(user_text) == "high":
        return crisis_reply(lang)

    emotions = safety.detect_emotions(user_text)
    items = content.retrieve(emotions, lang)

    if llm.provider() == "offline":
        reply = offline_reply(emotions, items, lang)
    else:
        system = SYSTEM_PROMPT.replace("{language}", LANG_NAME[lang]) + "\n" + _vetted_block(items)
        try:
            reply = llm.complete(system, history[-MAX_TURNS:])
        except Exception:
            reply = offline_reply(emotions, items, lang)
        if "[CRISIS]" in reply:
            return crisis_reply(lang)
        # Output check: block any religious reference not in the vetted library.
        if content.unverified_references(reply):
            reply = offline_reply(emotions, items, lang)

    return {"type": "support", "reply": reply, "emotions": emotions, "items": items,
            "resources": content.resources(lang)}
