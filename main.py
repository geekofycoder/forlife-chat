"""ForLife health companion, v0: a consultation. One chat per visitor, history kept in Postgres.

Every message from the patient goes through two steps:
  1. INTERVIEW  Claude answers like a doctor: a calm reply plus the next question, and a hidden confidence number.
  2. DECIDE     the server applies the stop rule below to that number. Until it says stop, the doctor's reply is sent
                as it is. When it says stop, a second call writes the final assessment (the cards).
"""
from dotenv import load_dotenv

load_dotenv(override=True) 

import json
import logging
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import anthropic
import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from prompts import CONCLUSION_PROMPT, CONCLUSION_SCHEMA, INTERVIEW_PROMPT, INTERVIEW_SCHEMA

log = logging.getLogger("forlife")
log.setLevel(logging.INFO)
if not log.handlers:
    log.addHandler(logging.StreamHandler())  # so the decision for each turn shows in the terminal

IST = ZoneInfo("Asia/Kolkata")
STATIC = Path(__file__).parent / "static"
MODEL = os.environ.get("CHAT_MODEL", "claude-sonnet-5-5")
EFFORT = os.environ.get("CHAT_EFFORT", "low")                 
INTERVIEW_EFFORT = os.environ.get("INTERVIEW_EFFORT", EFFORT)
REQUIRED = ["DATABASE_URL", "ANTHROPIC_API_KEY", "SHARED_PASSWORD"]
TRY_AGAIN = "I am facing some internal issues, don't worry I will come back to you and help you with your cure!"
CONFIDENCE_TO_CONCLUDE = 70  # confidence needed before the assessment is given
MIN_QUESTIONS = 3            # never give the assessment before this many questions have been asked
MAX_QUESTIONS = 8            # always give it once this many have been asked

REFUSAL = {"kind": "refusal", "reply": "Sorry, I can't help with that one. If this is urgent, please call 112 or see a doctor."}
HIDDEN = ("confidence", "reopened", "state")  # kept in the database for the server, never sent to the browser


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing in .env: {', '.join(missing)}")
    try:
        app.state.db = await asyncpg.create_pool(
            os.environ["DATABASE_URL"],
            statement_cache_size=0,               # needed behind Supabase's connection pooler
            min_size=1,
            max_size=5,
            command_timeout=8,                    # never hang on a stuck query (ours take milliseconds)
            timeout=10,                           # nor on opening a new connection
            max_inactive_connection_lifetime=60,  # drop idle connections before the pooler kills them
        )
    except OSError as e:  # includes "can't find the host" (DNS) and timeouts
        host = urlsplit(os.environ["DATABASE_URL"]).hostname
        raise RuntimeError(
            f"Can't reach the database host '{host}' ({e}). Check DATABASE_URL in .env and your internet connection."
        ) from e
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
    app.state.claude = anthropic.AsyncAnthropic(timeout=90,default_headers=headers)  # reads ANTHROPIC_API_KEY
    yield
    await app.state.db.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)  # no public API-docs pages


async def database_down(_request, exc):
    log.error("database error: %r", exc)
    return JSONResponse(
        {"detail": "I couldn't reach my memory just now. Please try again in a moment."},
        status_code=503,
    )


for exc_type in (asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError, ConnectionError):
    app.add_exception_handler(exc_type, database_down)

# Errors that mean "this pooled connection went stale", not "the query is wrong"
STALE_CONNECTION = (TimeoutError, ConnectionError, asyncpg.InterfaceError, asyncpg.PostgresConnectionError)


async def db_run(method: str, *args):
    """Run one query. If the pooled connection has gone stale, try once more on a fresh one."""
    try:
        return await getattr(app.state.db, method)(*args)
    except STALE_CONNECTION as e:
        log.warning("database %s failed (%r); retrying once", method, e)
        return await getattr(app.state.db, method)(*args)


def check_password(x_password: str = Header(default="")):
    if not secrets.compare_digest(x_password.encode(), os.environ["SHARED_PASSWORD"].encode()):
        raise HTTPException(401, "Wrong password")


# ---------- reading what was saved ----------

def as_dict(raw) -> dict:
    try:
        d = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def kind_of(d: dict) -> str:
    return d.get("kind") or ("conclusion" if d.get("conditions") else "interview")  # chats saved before 'kind' existed


def read_stage(rows) -> tuple[str, int]:
    """Replay the saved doctor turns: ('interviewing' or 'assessed') and how many questions this interview has asked."""
    stage, asked = "interviewing", 0
    for r in rows:
        if r["role"] != "assistant":
            continue
        d = as_dict(r["content"])
        kind = kind_of(d)
        if kind == "conclusion":
            stage, asked = "assessed", 0
        elif kind == "interview":
            if stage == "assessed":
                if d.get("reopened"):  # new information after the assessment: the doctor went back to asking
                    stage, asked = "interviewing", 1
            else:
                asked += 1
    return stage, asked


def spoken(raw: str) -> str:
    """What the doctor said, as plain text, for the transcript Claude sees (hidden notes and cards are not repeated)."""
    d = as_dict(raw)
    if not d:
        return raw or "..."
    if kind_of(d) == "conclusion":
        names = ", ".join(c.get("name", "") for c in d.get("conditions", []) if isinstance(c, dict))
        return f"{d.get('headline', '')} (I showed my assessment: {names}.)".strip()
    return d.get("reply") or d.get("headline") or d.get("message") or "..."  # last two: chats saved in earlier formats


def public(raw: str) -> str:
    """The saved reply without the server's hidden fields, as the browser gets it."""
    d = as_dict(raw)
    return json.dumps({k: v for k, v in d.items() if k not in HIDDEN}, ensure_ascii=False) if d else raw


def stamp(when: datetime, text: str) -> str:
    """'[10 Oct 2026, 09:12 AM IST] message': the date and time Claude sees on each user message."""
    return f"[{when.astimezone(IST):%d %b %Y, %I:%M %p} IST] {text}"


# ---------- the stop rule ----------

def decide(stage: str, asked_before: int, iv: dict) -> tuple[str, str]:
    """Returns (action, reason). action: 'emergency', 'ask' (send the doctor's next question), 'answer' (a follow-up
    after the assessment) or 'conclude' (write the assessment)."""
    confidence = iv["confidence"]
    if iv["emergency"]:
        return "emergency", "emergency"
    if stage == "assessed":
        if confidence < CONFIDENCE_TO_CONCLUDE:
            return "ask", "new information lowered confidence"
        return "answer", "follow-up"
    if iv["ready_now"]:
        return "conclude", "patient asked for an answer"
    if asked_before >= MAX_QUESTIONS:
        return "conclude", "maximum questions reached"
    if asked_before < MIN_QUESTIONS:
        return "ask", "minimum questions not reached"
    if confidence >= CONFIDENCE_TO_CONCLUDE:
        return "conclude", "confidence reached"
    return "ask", "confidence too low"


# ---------- talking to Claude ----------

async def ask_claude(system: str, messages: list, schema: dict, effort: str, max_tokens: int, ok):
    """One structured call. Returns the parsed answer, or None if Claude declined. Raises 502 if the answer is unusable."""
    t0 = time.time()
    try:
        resp = await app.state.claude.messages.create(
            model=MODEL,
            max_tokens=max_tokens,  # thinking is always on for this model and shares this budget with the answer
            system=system,
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            messages=messages,
        )
    except anthropic.APIError as e:
        log.error("Claude API error: %r", e)
        raise HTTPException(502, TRY_AGAIN)
    if resp.stop_reason == "refusal":
        return None
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict) or not ok(data):
        log.error("Claude reply was not usable (stop_reason=%s)", resp.stop_reason)
        raise HTTPException(502, TRY_AGAIN)
    data["_seconds"] = round(time.time() - t0, 1)
    return data


def interview_ok(d: dict) -> bool:
    return isinstance(d.get("reply"), str) and bool(d["reply"].strip()) and isinstance(d.get("emergency"), bool)


def conclusion_ok(d: dict) -> bool:
    return isinstance(d.get("headline"), str) and isinstance(d.get("conditions"), list) and bool(d["conditions"])


def percent(v) -> int:
    try:
        return max(0, min(100, int(v)))
    except (TypeError, ValueError):
        return 0


class ChatIn(BaseModel):
    visitor_id: uuid.UUID  # the browser's "cloakroom ticket"
    text: str = Field(max_length=4000)


@app.get("/", include_in_schema=False)
def page():
    return FileResponse(STATIC / "index.html")


@app.get("/history", dependencies=[Depends(check_password)])
async def history(visitor_id: uuid.UUID):
    rows = await db_run(
        "fetch", "select role, content, created_at from messages where visitor_id = $1 order by id", visitor_id
    )
    return [
        {"role": r["role"], "content": public(r["content"]) if r["role"] == "assistant" else r["content"],
         "at": r["created_at"].isoformat()}
        for r in rows
    ]


@app.post("/chat", dependencies=[Depends(check_password)])
async def chat(body: ChatIn):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Please type a message.")
    asked = datetime.now(IST)

    # 1. load this visitor's earlier messages and work out where the consultation is
    rows = await db_run(
        "fetch", "select role, content, created_at from messages where visitor_id = $1 order by id", body.visitor_id
    )
    stage, asked_before = read_stage(rows)

    # 2. the transcript Claude sees: what the patient wrote (with date and time) and what the doctor said
    messages = [
        {"role": r["role"], "content": stamp(r["created_at"], r["content"]) if r["role"] == "user" else spoken(r["content"])}
        for r in rows
    ]
    messages.append({"role": "user", "content": stamp(asked, text)})

    # 3. interview step: the doctor's reply plus a hidden confidence; then the stop rule decides what to send
    stage_line = (
        "STAGE: you are still taking the history."
        if stage == "interviewing"
        else "STAGE: the assessment has already been given; the patient is following up."
    )
    iv = await ask_claude(f"{INTERVIEW_PROMPT}\n{stage_line}", messages, INTERVIEW_SCHEMA, INTERVIEW_EFFORT, 6000, interview_ok)
    if iv is None:
        content = REFUSAL
    else:
        iv["confidence"] = percent(iv.get("confidence"))
        iv["ready_now"] = bool(iv.get("ready_now"))
        action, reason = decide(stage, asked_before, iv)
        log.info(
            "question %d | confidence %d (need %d) | %s: %s | %ss",
            asked_before + 1, iv["confidence"], CONFIDENCE_TO_CONCLUDE, action, reason, iv["_seconds"],
        )
        if action == "conclude":
            cc = await ask_claude(CONCLUSION_PROMPT, messages, CONCLUSION_SCHEMA, EFFORT, 8000, conclusion_ok)
            log.info("assessment written in %ss", cc["_seconds"] if cc else "-")
            if cc:
                cc.pop("_seconds")
            content = REFUSAL if cc is None else {"kind": "conclusion", **cc, "confidence": iv["confidence"]}
        elif action == "emergency":
            content = {"kind": "emergency", "reply": iv["reply"], "confidence": iv["confidence"]}
        else:
            content = {
                "kind": "interview", "reply": iv["reply"], "confidence": iv["confidence"],
                "reopened": stage == "assessed" and action == "ask",
            }
    reply = json.dumps(content, ensure_ascii=False)  # stored as-is, hidden fields included

    # 4. save both sides, send the reply (without hidden fields) back to the page
    answered = datetime.now(IST)
    save = (
        "insert into messages (visitor_id, role, content, created_at) "
        "values ($1, 'user', $2, $4), ($1, 'assistant', $3, $5)",
        body.visitor_id, text, reply, asked, answered,
    )
    try:
        await app.state.db.execute(*save)
    except STALE_CONNECTION as e:
        log.warning("database save failed (%r); checking, then retrying once", e)
        # the first try may have been saved just before the connection died: look before saving again
        if not await db_run("fetchval", "select 1 from messages where visitor_id = $1 and role = 'user' and created_at = $2",
                            body.visitor_id, asked):
            await app.state.db.execute(*save)
    return {"reply": public(reply), "at": answered.isoformat()}


app.mount("/static", StaticFiles(directory=STATIC), name="static")
