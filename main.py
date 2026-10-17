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
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import anthropic
import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import doctors
import family
import places
import reviews
from prompts import (
    CONCLUSION_PROMPT, CONCLUSION_SCHEMA, CONFIDENCE_TO_CONCLUDE, EMERGENCY_NOTE, GROUP_RULES, GROUP_TITLES,
    INTERVIEW_PROMPT, INTERVIEW_SCHEMA, REOPEN_BELOW, TERMS_PROMPT, TERMS_SCHEMA,
)

log = logging.getLogger("forlife")
log.setLevel(logging.INFO)
if not log.handlers:
    log.addHandler(logging.StreamHandler())  # so the decision for each turn shows in the terminal

IST = ZoneInfo("Asia/Kolkata")
STATIC = Path(__file__).parent / "static"
MODEL = os.environ.get("CHAT_MODEL", "claude-sonnet-5-5")
EFFORT = os.environ.get("CHAT_EFFORT", "low")                 
INTERVIEW_EFFORT = os.environ.get("INTERVIEW_EFFORT", EFFORT)
TERMS_MODEL = os.environ.get("TERMS_MODEL", "claude-haiku-5-5")   # writes the words used to look for conditions in reviews (a small, quick job)
REQUIRED = ["DATABASE_URL", "ANTHROPIC_API_KEY", "SHARED_PASSWORD"]
TRY_AGAIN = "I am facing some internal issues, don't worry I will come back to you and help you with your cure!"
# CONFIDENCE_TO_CONCLUDE (70) and REOPEN_BELOW (60) live in prompts.py, next to the prompt text that quotes them.
MIN_QUESTIONS = 4            # never give the assessment before this many questions have been asked
MAX_QUESTIONS = 8            # always give it once this many have been asked

MEMBER_COLUMNS = "id, relation, name, dob, sex, conditions, medicines, allergies, pincode"
# Each member's own doctors as a JSON list, added to every member query ({m} is the members alias)
DOCTORS_JSON = (
    "coalesce((select jsonb_agg(jsonb_build_object('role', d.role, 'name', d.name, 'phone', d.phone, 'address', d.address) "
    "order by d.role) from member_doctors d where d.member_id = {m}.id), '[]'::jsonb)::text"
)
MEMBER_SELECT = f"select {', '.join('m.' + c for c in MEMBER_COLUMNS.split(', '))}, {DOCTORS_JSON.format(m='m')} as doctors from members m"
MEMBERS_QUERY = f"{MEMBER_SELECT} where m.visitor_id = $1 order by m.created_at"
REFUSAL = {"kind": "refusal", "reply": "Sorry, I can't help with that one. If this is urgent, please call 112 or see a doctor."}
HIDDEN = ("confidence", "reopened", "state", "patient")  # kept in the database for the server, never sent to the browser


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


async def load_members(visitor_id):
    """This visitor's family profiles. If the table has not been created yet, carry on without a family file."""
    try:
        return await db_run("fetch", MEMBERS_QUERY, visitor_id)
    except asyncpg.UndefinedTableError:
        log.warning("The members table does not exist yet: run schema.sql in the Supabase SQL editor. Continuing without a family file.")
        return []


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


def last_patient(rows) -> str:
    """The person the doctor last said the chat was about (empty if never identified)."""
    for r in reversed(rows):
        if r["role"] == "assistant":
            who = as_dict(r["content"]).get("patient")
            if who:
                return who
    return ""


def spoken(raw: str) -> str:
    """What the doctor said, as plain text, for the transcript Claude sees (hidden notes and cards are not repeated)."""
    d = as_dict(raw)
    if not d:
        return raw or "..."
    if kind_of(d) == "conclusion":
        names = ", ".join(c.get("name", "") for c in d.get("conditions", []) if isinstance(c, dict))
        who = f" for {d['patient_label']}" if d.get("patient_label") else ""
        lead = f"{d['reply']} " if d.get("reply") else ""  # an emergency assessment starts with the emergency message
        return f"{lead}{d.get('headline', '')} (I showed my assessment{who}: {names}.)".strip()
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
        if iv.get("switched"):
            return "ask", "a different person"
        if confidence < REOPEN_BELOW:
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

async def ask_claude(system: str, messages: list, schema: dict, effort: str, max_tokens: int, ok, model: str | None = None):
    """One structured call. Returns the parsed answer, or None if Claude declined. Raises 502 if the answer is unusable."""
    t0 = time.time()
    try:
        resp = await app.state.claude.messages.create(
            model=model or MODEL,
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


async def write_assessment(members, file_text, blocks, messages, iv, today, emergency=False):
    """The full assessment, as it is stored and shown. Returns None if Claude declined.
    In an emergency the emergency message stays on top and every condition is immediate."""
    member = family.match_patient(members, iv["patient"])
    if member:
        a = family.describe(member["dob"], today)
        floor = family.FLOORS.get(a["group"])
        who = f"PATIENT FOR THIS ASSESSMENT: {member['name']}, {a['text']} ({a['label']})." + (
            f" Group floor: every condition at least {floor}." if floor else ""
        )
    else:
        who = "PATIENT FOR THIS ASSESSMENT: not identified; use the person the latest complaint is about."
    parts = [CONCLUSION_PROMPT, file_text, blocks, who] + ([EMERGENCY_NOTE] if emergency else [])
    cc = await ask_claude("\n\n".join(p for p in parts if p), messages, CONCLUSION_SCHEMA, EFFORT, 8000, conclusion_ok)
    log.info("assessment written in %ss%s", cc["_seconds"] if cc else "-", " (emergency)" if emergency else "")
    if cc is None:
        return None
    cc.pop("_seconds")
    pick = cc.get("doctor")
    cc["doctor"] = doctors.card(pick)  # name and plain-words description come from doctors.py
    if not cc["doctor"]:
        log.warning("doctor pick is not on the list, card dropped: %r", pick)
    # their own family doctor / paediatrician / gynaecologist, from their profile (never from Claude)
    cc["doctor"] = doctors.with_own(cc["doctor"], member, family.member_doctors(member) if member else [], emergency)
    extra = {}
    if member:
        raised = family.apply_floor(cc["conditions"], a["group"])  # backstop, not clinically reviewed
        if raised:
            log.info("risk floor applied (%s): %s", a["group"], "; ".join(raised))
        first = member["name"].split()[0]
        extra = {"patient": first, "patient_label": f"{first} · {a['text']} ({a['label']})"}
    if emergency:
        raised = family.raise_risk(cc["conditions"], "immediate")  # the prompt asks for this; the code makes sure
        if raised:
            log.info("emergency: risk raised to immediate: %s", "; ".join(raised))
        extra |= {"emergency": True, "reply": iv["reply"]}
    return {"kind": "conclusion", **cc, **extra, "confidence": iv["confidence"]}


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


class DoctorIn(BaseModel):
    role: str = Field(max_length=20)  # family, paediatrician or gynaecologist
    name: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=40)
    address: str = Field(default="", max_length=400)


class MemberIn(BaseModel):
    visitor_id: uuid.UUID
    relation: str
    name: str = Field(max_length=200)
    dob: date
    sex: str
    conditions: str = Field(default="", max_length=2000)
    medicines: str = Field(default="", max_length=2000)
    allergies: str = Field(default="", max_length=2000)
    pincode: str = Field(default="", max_length=20)   # asked on the account holder's own profile, for "doctors near you"
    doctors: list[DoctorIn] = Field(default=[], max_length=3)


class EditIn(BaseModel):
    """What can be changed on a saved profile: health details, pincode and their own doctors (not name, age or relation)."""
    visitor_id: uuid.UUID
    conditions: str = Field(default="", max_length=2000)
    medicines: str = Field(default="", max_length=2000)
    allergies: str = Field(default="", max_length=2000)
    pincode: str | None = Field(default=None, max_length=20)   # left out = unchanged; empty = cleared
    doctors: list[DoctorIn] = Field(default=[], max_length=3)


@app.get("/family", dependencies=[Depends(check_password)])
async def get_family(visitor_id: uuid.UUID):
    try:
        rows = await db_run("fetch", MEMBERS_QUERY, visitor_id)
    except asyncpg.UndefinedTableError:
        raise HTTPException(503, "Family profiles are not set up yet. Please tell whoever runs this app.")
    today = datetime.now(IST).date()
    members = [family.public_member(r, today) for r in rows]
    me = next((m for m in members if m["relation"] == "self"), None)
    return {"members": members, "needs_profile": me is None, "needs_pincode": me is not None and not me["pincode"]}


@app.post("/members", dependencies=[Depends(check_password)])
async def add_member(body: MemberIn):
    today = datetime.now(IST).date()
    try:
        m = family.clean_member(body, today)
        own = family.clean_doctors(body.doctors, m["dob"], m["sex"], today)
    except ValueError as e:
        raise HTTPException(422, str(e))
    # One database trip: add the person (and their own doctors) unless the same person (relation, name, date of birth,
    # gender) is already there, in which case that profile is returned unchanged, so a double click or a retry can't
    # create a duplicate. The cap is checked here too.
    try:
        row = await app.state.db.fetchrow(
            f"with same as (select {MEMBER_COLUMNS} from members where visitor_id = $1::uuid and relation = $2::text "
            f"and lower(name) = lower($3::text) and dob = $4::date and sex = $5::text limit 1), "
            f"added as (insert into members (visitor_id, relation, name, dob, sex, conditions, medicines, allergies, pincode) "
            f"select $1::uuid, $2::text, $3::text, $4::date, $5::text, $6::text, $7::text, $8::text, $10::text "
            f"where not exists (select 1 from same) and (select count(*) from members where visitor_id = $1::uuid) < {family.MAX_MEMBERS} "
            f"returning {MEMBER_COLUMNS}), "
            f"m as (select * from added union all select * from same), "
            f"docs as (insert into member_doctors (visitor_id, member_id, role, name, phone, address) "
            f"select $1::uuid, a.id, d.role, d.name, d.phone, d.address from added a, "
            f"jsonb_to_recordset($9::jsonb) as d(role text, name text, phone text, address text) returning role, name, phone, address) "
            f"select m.*, coalesce((select jsonb_agg(jsonb_build_object('role', x.role, 'name', x.name, 'phone', x.phone, "
            f"'address', x.address) order by x.role) from docs x)::text, {DOCTORS_JSON.format(m='m')}) as doctors from m",
            body.visitor_id, m["relation"], m["name"], m["dob"], m["sex"], m["conditions"], m["medicines"], m["allergies"],
            json.dumps(own), m["pincode"],
        )
    except asyncpg.UniqueViolationError as e:
        if e.constraint_name == "members_one_self_per_visitor":
            raise HTTPException(409, "Your own profile is already set up.")
        # members_no_duplicate_people: another request saved this same person a moment ago (two tabs at the same instant)
        row = await db_run(
            "fetchrow",
            f"{MEMBER_SELECT} where m.visitor_id = $1 and m.relation = $2 and lower(m.name) = lower($3) and m.dob = $4 and m.sex = $5",
            body.visitor_id, m["relation"], m["name"], m["dob"], m["sex"],
        )
        if row is None:
            raise HTTPException(409, "That person is already added.")
    if row is None:
        raise HTTPException(409, f"You can add up to {family.MAX_MEMBERS} people.")
    return family.public_member(row, today)


@app.put("/members/{member_id}", dependencies=[Depends(check_password)])
async def edit_member(member_id: uuid.UUID, body: EditIn):
    today = datetime.now(IST).date()
    who = await db_run("fetchrow", "select dob, sex from members where id = $1 and visitor_id = $2", member_id, body.visitor_id)
    if who is None:
        raise HTTPException(404, "That person isn't in your family list.")
    try:
        own = family.clean_doctors(body.doctors, who["dob"], who["sex"], today)
        pincode = family.clean_pincode(body.pincode)
    except ValueError as e:
        raise HTTPException(422, str(e))
    # One statement: update the health details (and the pincode, if one was sent), drop the doctors they removed, add or update the rest.
    row = await app.state.db.fetchrow(
        f"with m as (update members set conditions = $3::text, medicines = $4::text, allergies = $5::text, "
        f"pincode = case when $8::boolean then $9::text else pincode end "
        f"where id = $1::uuid and visitor_id = $2::uuid returning {MEMBER_COLUMNS}), "
        f"gone as (delete from member_doctors where member_id = $1::uuid and visitor_id = $2::uuid and not (role = any($6::text[]))), "
        f"up as (insert into member_doctors (visitor_id, member_id, role, name, phone, address) "
        f"select $2::uuid, m.id, d.role, d.name, d.phone, d.address from m, "
        f"jsonb_to_recordset($7::jsonb) as d(role text, name text, phone text, address text) "
        f"on conflict (member_id, role) do update set name = excluded.name, phone = excluded.phone, address = excluded.address, "
        f"updated_at = now() returning role, name, phone, address) "
        f"select m.*, coalesce((select jsonb_agg(jsonb_build_object('role', u.role, 'name', u.name, 'phone', u.phone, "
        f"'address', u.address) order by u.role) from up u), '[]'::jsonb)::text as doctors from m",
        member_id, body.visitor_id, family.clean_text(body.conditions)[:500], family.clean_text(body.medicines)[:500],
        family.clean_text(body.allergies)[:500], [d["role"] for d in own], json.dumps(own),
        body.pincode is not None, pincode or None,
    )
    if row is None:
        raise HTTPException(404, "That person isn't in your family list.")
    return family.public_member(row, today)


_terms_memo: dict = {}   # condition name -> the words used to look for it in reviews (written once per name while the server runs)


async def review_terms(names: list) -> list:
    """The search words for each condition name, written by a small model. Any failure gives the name's own words instead, never an error."""
    todo = [n for n in dict.fromkeys(names) if n.lower() not in _terms_memo]
    if todo:
        try:
            body = "\n".join(f"{i + 1}) {n}" for i, n in enumerate(todo))
            data = await ask_claude(TERMS_PROMPT, [{"role": "user", "content": body}], TERMS_SCHEMA, "low", 1500,
                                    lambda d: isinstance(d.get("conditions"), list), model=TERMS_MODEL)
            for n, c in zip(todo, data["conditions"]):
                if isinstance(c, dict):
                    _terms_memo[n.lower()] = reviews.clean_terms(c.get("terms"))
            log.info("review terms written in %ss for %d condition(s)", data["_seconds"], len(todo))
        except Exception as e:   # includes the HTTPException ask_claude raises
            log.warning("review terms not written, using the names' own words: %r", e)
    if len(_terms_memo) > 500:
        _terms_memo.clear()
    return [_terms_memo.get(n.lower()) or reviews.terms_from_name(n) for n in names]


def read_conditions(raw: str) -> list:
    """The assessment's conditions as the page sent them ([{t: tier, n: name}]), kept only if they look right (at most 5)."""
    if not isinstance(raw, str):
        return []
    try:
        items = json.loads(raw) if raw else []
    except ValueError:
        return []
    out = []
    for x in items[:5] if isinstance(items, list) else []:
        if isinstance(x, dict) and x.get("t") in reviews.TIER_WEIGHT and isinstance(x.get("n"), str):
            name = family.clean_text(x["n"])[:120]
            if name:
                out.append({"tier": x["t"], "name": name})
    return out


@app.get("/doctors/nearby", dependencies=[Depends(check_password)])
async def doctors_nearby(visitor_id: uuid.UUID, doctor: str, conditions: str = Query(default="", max_length=1500)):
    """The best two doctors or clinics of this kind near the account holder's pincode (looked up here, never taken from the page).
    With `conditions` (the assessment's, as JSON) their reviews are read and doctors whose reviews mention those conditions move up."""
    key = doctor.strip().lower()
    if key not in doctors.DOCTORS or key not in places.SEARCH_PHRASE:
        raise HTTPException(422, "I can't look that kind of doctor up yet.")
    pincode = await db_run("fetchval", "select pincode from members where visitor_id = $1 and relation = 'self'", visitor_id)
    if not pincode:
        raise HTTPException(409, "Please add your pincode to your profile first, and I'll find doctors near you.")
    try:
        found = await places.find_nearby(db_run, pincode, key, conditions=read_conditions(conditions), terms_for=review_terms)
    except places.NotConfigured:
        raise HTTPException(503, "Finding doctors near you isn't set up yet. Please tell whoever runs this app.")
    except places.ProviderError as e:
        log.error("nearby search failed: %s", e)
        raise HTTPException(502, "I couldn't look up doctors near you just now. Please try again in a moment.")
    return {"pincode": pincode, "doctor": doctors.DOCTORS[key]["name"], "results": found["results"], "stale": found["source"] == "stale"}


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
    members = await load_members(body.visitor_id)
    today = asked.date()
    file_text = family.family_file(members, today)               # who is in the family, ages, conditions, medicines, allergies
    blocks = family.group_blocks(members, GROUP_RULES, GROUP_TITLES, today)
    prev_patient = last_patient(rows)

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
    system = "\n\n".join(part for part in (INTERVIEW_PROMPT, file_text, blocks, stage_line) if part)
    iv = await ask_claude(system, messages, INTERVIEW_SCHEMA, INTERVIEW_EFFORT, 6000, interview_ok)
    if iv is None:
        content = REFUSAL
    else:
        iv["confidence"] = percent(iv.get("confidence"))
        iv["ready_now"] = bool(iv.get("ready_now"))
        iv["patient"] = family.clean_text(iv.get("patient"))
        iv["switched"] = bool(iv["patient"] and prev_patient and iv["patient"].casefold() != prev_patient.casefold())
        action, reason = decide(stage, asked_before, iv)
        log.info(
            "question %d | patient %s | confidence %d (need %d) | %s: %s | %ss",
            asked_before + 1, iv["patient"] or "?", iv["confidence"], CONFIDENCE_TO_CONCLUDE, action, reason, iv["_seconds"],
        )
        if action == "conclude":
            content = await write_assessment(members, file_text, blocks, messages, iv, today) or REFUSAL
        elif action == "emergency":
            content = {"kind": "emergency", "reply": iv["reply"], "confidence": iv["confidence"], "patient": iv["patient"]}
            if stage == "interviewing":  # one assessment per case: a repeat emergency shows just the emergency message
                try:
                    content = await write_assessment(members, file_text, blocks, messages, iv, today, emergency=True) or content
                except HTTPException:  # never lose the emergency message because the assessment failed
                    log.warning("emergency assessment failed; sending the emergency message on its own")
        else:
            content = {
                "kind": "interview", "reply": iv["reply"], "confidence": iv["confidence"], "patient": iv["patient"],
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
