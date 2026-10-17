# ForLife Health Companion

https://forlife-chat.onrender.com/

A calm, doctor-like health chat for Indian families. It takes a short history one question at a time, then gives a ranked assessment with when to see a doctor.

**Live demo:** _link added after the first deploy_ (password shared separately)

> Demo only. An AI companion, not a doctor. Use made-up details. In an emergency, call 112.

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in the values
uvicorn main:app --port 8000
```

Open http://127.0.0.1:8000. Create the table once by running `schema.sql` in the Supabase SQL Editor.

## Settings (`.env`, never committed)

| Variable | What it is |
|---|---|
| `DATABASE_URL` | Supabase "Session pooler" connection string |
| `ANTHROPIC_API_KEY` | Key from the Anthropic Console |
| `ANTHROPIC_WORKSPACE_ID` | Only if the key is not tied to one workspace |
| `SHARED_PASSWORD` | The one password shared with testers |
| `SERPER_API_KEY` | Serper.dev key for "Find doctors near you" (optional: without it that button says it isn't set up yet) |
| `CHAT_MODEL`, `CHAT_EFFORT`, `INTERVIEW_EFFORT` | Optional |

## How it works

- `prompts.py` holds the doctor's instructions: one prompt for the questions, one for the final assessment.
- `main.py` keeps each visitor's chat in Postgres and decides when to stop asking (`CONFIDENCE_TO_CONCLUDE`, `MIN_QUESTIONS`, `MAX_QUESTIONS`).
- `static/index.html` is the chat page.
- `places.py` finds doctors near the account holder's pincode (Google Maps through Serper.dev): it filters and ranks the listings, caches them in `nearby_cache`, and makes sure a double press is one search. The Serper call is one function, so another provider can replace it.

Adding a pincode to a database that already has `members`: run the `alter table ... add column pincode` line from `schema.sql` (and create `nearby_cache`) **before** deploying this code.


