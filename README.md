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
- `reviews.py` reads the Google reviews of the doctors `places.py` finds (up to 20 per place, text and stars only) and boosts the ones whose reviews mention the patient's likely conditions. Claude Haiku writes the search words from the condition names (`prompts.py`: `TERMS_PROMPT`); the matching is plain dictionary matching, and it only ever moves doctors up (more matching reviews and more different words count for more; no matches costs nothing). The weights (`TIER_WEIGHT`, `MAX_BONUS`, `SOFTNESS`) are at the top of the file.
- `shop.py` powers the "Things that may help" box under an assessment: a fixed, short list of self-care items (ORS, a heating pad, a thermometer...), never medicines. A small model (Haiku) picks keys from that list for the patient and code removes what doesn't suit their age. Example listings come from Google Shopping through Serper (2 credits per new item and pincode), shown as cards that link to Google Shopping, with quick-delivery apps first. Results are cached in `nearby_cache` under `shop:<item>` for a day. The item list needs a clinician's review before real use.
- `static/blue_door/` is a "fake door" test: the "Prefer to talk?" pane in the header opens a dialog with two doors, "Talk with Vera" and "Reach out to a ForLife specialist". Neither exists yet; each opens a "Coming soon" note and the tap is counted at `POST /interest` (`main.py`). Counts go to the optional `interest_events` table in `schema.sql` (without it they are only in the server log). Vera and the doctor are vector drawings (`vera.svg`, `doctor.svg`); any transparent 800 x 1040 image can replace them. To remove the test, delete the folder, then in `static/index.html` the pane button in `<header>`, the `<link rel="stylesheet" href="/static/blue_door/blue_door.css">` in `<head>`, and the dialog and two `<script>` tags at the end of the page.
- `places.py` finds doctors near the account holder's pincode (Google Maps through Serper.dev): it filters and ranks the listings, caches them in `nearby_cache`, and makes sure a double press is one search. The Serper call is one function, so another provider can replace it.

Adding a pincode to a database that already has `members`: run the `alter table ... add column pincode` line from `schema.sql` (and create `nearby_cache`) **before** deploying this code.


