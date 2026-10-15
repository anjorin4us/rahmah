# RAHMAH MVP

Anonymous wellbeing support for IIUM students, grounded in Islamic psychology.
Gold award, IIUM Festival of Ideas (TAKRIM 2025).

The MVP has two modes, controlled by one setting:

| `APP_MODE` | Who it is for | What students see |
| --- | --- | --- |
| `demo` (default) | Partners, judges, funders, testers | All content not rejected, marked "draft, awaiting review"; a demo banner; a warning not to enter real phone numbers |
| `pilot` | Consenting IIUM students, **after ethics approval** | Only content approved by KIRKHS (religious) or CCSC (exercises); no demo banner |

## What is in it

**For students** (`/`)
- Anonymous chat in **English or Bahasa Melayu**: no login, no cookies, nothing stored on the server.
- Crisis screen before the AI (English, Malay, Arabic). A crisis message never reaches the model and gets fixed helpline contacts.
- Vetted Qur'an, hadith, du'a and exercises shown as cards, in the chosen language.
- "Was this helpful?" buttons. Only yes/no, language and the day are stored, never the message.
- **Optional callback request**: the student can leave a Malaysian mobile number so CCSC calls back. It requires explicit consent, only CCSC can see it, and it is deleted automatically after 14 days.
- Optional link to your anonymous pilot survey.

**For staff** (`/admin`)
- **KIRKHS** reviews Qur'an, hadith and du'a; **CCSC** reviews exercises. Each item can be edited (English, Malay, Arabic, tags), approved, sent back for changes or rejected, and new items can be added. Every decision records the reviewer's name. Editing the wording of an approved item sends it back to draft.
- **CCSC** sees callback requests, can tap to call, and marks each one contacted or closed.
- Both teams see anonymous feedback counts.

## Run it on your laptop (5 minutes)

```bash
pip install -r requirements.txt
cp .env.example .env            # then edit the passwords
export $(grep -v '^#' .env | xargs)
python -m uvicorn rahmah.app:app --port 8000
```
Open http://localhost:8000 (student) and http://localhost:8000/admin (staff: team `kirkhs` or `ccsc` + its password).

With no `GROQ_API_KEY` it runs in **offline mode**: replies are built from the vetted library. That is enough for a demo with no internet AI at all.

## Put it online for free (about 20 minutes)

You need three free accounts: GitHub, Neon (database) and Render (hosting). Optionally Groq (AI model).

1. **Code → GitHub.** Create a *private* repository and upload this folder (or `git push` it).
2. **Database → Neon.** Sign up at neon.com, create a project (choose the Singapore region if offered), and copy the connection string (`postgresql://...`). Neon's free plan does not expire. (Render's own free database is deleted after 30 days, so it is not used here.)
3. **AI model → Groq (optional).** Sign up at console.groq.com and create an API key. With the key, RAHMAH uses the free open-weight **Llama 3.3 70B** model; without it, offline mode.
4. **Hosting → Render.** Sign up at render.com with GitHub, then **New → Blueprint** and pick your repository. Render reads `render.yaml` and asks for:
   - `DATABASE_URL`: the Neon connection string
   - `GROQ_API_KEY`: your Groq key (or leave blank)
   - `SURVEY_URL`: optional
   Render generates the KIRKHS and CCSC passwords; find them under the service's **Environment** tab and share them privately with each team.
5. Open the `https://rahmah-xxxx.onrender.com` link Render gives you.

**Free-tier behaviour to know:** the app sleeps after 15 minutes without visitors and takes about a minute to wake. Open the link a few minutes before a demo. Groq's free tier allows about 1,000 requests a day on Llama 3.3 70B, plenty for demos.

## Switching to the pilot

Do not switch until **all** of these are done:
- [ ] IIUM Research Ethics Committee approval
- [ ] KIRKHS has approved the religious items and CCSC the exercises (check counts on `/admin`)
- [ ] CCSC has reviewed and extended the crisis phrases in `rahmah/safety.py` and the red-team set in `tests/redteam.jsonl`, and all tests pass
- [ ] CCSC has agreed who checks callback requests and how often
- [ ] A data protection officer is named and the privacy notice is approved (PDPA)
- [ ] The AI runs with a zero-data-retention agreement or on a self-hosted model (Ollama; see `.env.example`)

Then set `APP_MODE=pilot` on Render. No code change is needed.

## Test it

```bash
python -m pytest -q                       # SQLite
TEST_DATABASE_URL=postgresql://... python -m pytest -q   # also against Postgres
```
41 tests cover the crisis routing (red-team set in three languages), the quote check, Malay replies, demo vs pilot content, the role permissions, feedback storing no text, callback consent and phone validation, and automatic deletion of old callback requests, and the fallback when the AI service is down or over its free limit.

## Privacy by design

- No accounts or cookies; `Cache-Control: no-store`; server access logs are switched off (`--no-access-log`) so IP addresses are not recorded.
- Conversations stay in the student's browser and are sent only to answer the current message.
- The database never holds conversation text. Callback numbers exist only when a student opts in, and are purged after `CALLBACK_RETENTION_DAYS`.
- Rate limiting keeps IP addresses in memory for one minute only.

## Files

| Path | Purpose |
| --- | --- |
| `rahmah/safety.py` | Crisis screen and emotion tagging |
| `rahmah/chat.py` | Message pipeline, system prompt, English and Malay reply text |
| `rahmah/content.py` | Content retrieval, demo/pilot filtering, quote check |
| `rahmah/llm.py` | Model interface: Groq, any OpenAI-compatible API, Ollama, Claude, or offline |
| `rahmah/db.py` | Database tables: content, feedback counts, callback requests |
| `rahmah/app.py` | Web server and API, staff login and permissions |
| `static/index.html` | Student chat (EN/BM) |
| `static/admin.html` | Staff review and callback pages |
| `content/library.json` | Seed content (draft) with Malay translations; loaded into the database on first run |
| `content/resources.json` | Helplines, verified October 2026 |
| `render.yaml`, `Dockerfile` | Free cloud deploy, or Docker on an IIUM/private server |
