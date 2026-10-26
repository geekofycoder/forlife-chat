# ForLife Health Companion

https://forlife-chat.onrender.com/

A calm, doctor-like health chat for Indian families. It takes a short history one question at a time, then gives a ranked assessment with when to see a doctor.

**Live demo:** _link added after the first deploy_ (sign in with Google)

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
| `SUPABASE_URL` | Your Supabase project's web address, `https://<project-ref>.supabase.co` (not a secret). People sign in with Google through Supabase Auth; the server checks each sign-in against the project's public key, so no extra secret is needed. Set-up: Google Cloud Console > create an OAuth client (Web application); Supabase dashboard > Authentication > Sign In / Providers > Google (paste the client ID and secret) and > URL Configuration (Site URL, plus the redirect URLs `https://forlife-chat.onrender.com/**` and `http://localhost:8000/**`) |
| `SERPER_API_KEY` | Serper.dev key for "Find doctors near you" (optional: without it that button says it isn't set up yet) |
| `CHAT_MODEL`, `CHAT_EFFORT`, `INTERVIEW_EFFORT` | Optional |

## How it works

- `prompts.py` holds the doctor's instructions: one prompt for the questions, one for the final assessment.
- `main.py` keeps each visitor's chat in Postgres and decides when to stop asking (`CONFIDENCE_TO_CONCLUDE`, `MIN_QUESTIONS`, `MAX_QUESTIONS`).
- `static/index.html` is the chat page.
- **Sign-in** is "Continue with Google" through Supabase Auth (more methods can be added later without touching the server). `static/vendor/` holds a pinned, integrity-checked copy of `supabase-js`, so the page loads no sign-in code from another site. The page sends the signed-in person's token with every call (`Authorization: Bearer`); `main.py` checks it against your project's public key (`current_user`) and uses the user id as the owner of the data (the database column is still called `visitor_id`). The browser never says who it is: a `visitor_id` in a request is ignored. `POST /claim` moves what a browser saved under its old random id (from before sign-in existed) into the account the first time someone signs in, only into an empty account and only once per old id.
- `reviews.py` reads the Google reviews of the doctors `places.py` finds (up to 20 per place, text and stars only) and boosts the ones whose reviews mention the patient's likely conditions. Claude Haiku writes the search words from the condition names (`prompts.py`: `TERMS_PROMPT`); the matching is plain dictionary matching, and it only ever moves doctors up (more matching reviews and more different words count for more; no matches costs nothing). The weights (`TIER_WEIGHT`, `MAX_BONUS`, `SOFTNESS`) are at the top of the file.
- `shop.py` powers the "Things that may help" box under an assessment: a fixed, short list of self-care items (ORS, a heating pad, a thermometer...), never medicines. A small model (Haiku) picks keys from that list for the patient and code removes what doesn't suit their age. Example listings come from Google Shopping through Serper (2 credits per new item and pincode), shown as cards that link to Google Shopping, with quick-delivery apps first. Results are cached in `nearby_cache` under `shop:<item>` for a day. The item list needs a clinician's review before real use.
- `static/blue_door/` is a "fake door" test: the "Prefer to talk?" pane in the header opens a dialog with two doors, "Talk with Vera" and "Reach out to a ForLife specialist". Neither exists yet; each opens a "Coming soon" note and the tap is counted at `POST /interest` (`main.py`). Counts go to the optional `interest_events` table in `schema.sql` (without it they are only in the server log). Vera and the doctor are vector drawings (`vera.svg`, `doctor.svg`); any transparent 800 x 1040 image can replace them. To remove the test, delete the folder, then in `static/index.html` the pane button in `<header>`, the `<link rel="stylesheet" href="/static/blue_door/blue_door.css">` in `<head>`, and the dialog and two `<script>` tags at the end of the page.
- `places.py` finds doctors near the account holder's pincode (Google Maps through Serper.dev): it filters and ranks the listings, caches them in `nearby_cache`, and makes sure a double press is one search. The Serper call is one function, so another provider can replace it.

Adding a pincode to a database that already has `members`: run the `alter table ... add column pincode` line from `schema.sql` (and create `nearby_cache`) **before** deploying this code.


