"""Nearby doctors: search a pincode on Google Maps (through Serper.dev), keep the good listings, and cache them.

The pieces, top to bottom:
  serper_search   the ONLY provider-specific code. It returns plain places; to use Google Places instead, replace this one
                  function (and keep its output shape).
  choose          pure rules: drop what isn't a doctor or clinic, drop far or unrated listings, rank by reviews, keep the best five.
  find_nearby     the cache (table nearby_cache) plus single-flight, so a double press makes one search, not two.
Only cleaned fields are stored. NOT clinically reviewed; these are public Google listings, not recommendations.
"""
import asyncio
import json
import logging
import math
import os
import re
import statistics
import urllib.error
import urllib.request
from collections import defaultdict

import reviews as rv

log = logging.getLogger("forlife")

SERPER_URL = "https://google.serper.dev/maps"
SERPER_REVIEWS_URL = "https://google.serper.dev/reviews"
SERPER_TIMEOUT = 12            # seconds; a normal search takes 2-3
FRESH_DAYS = 7                 # a stored answer this young is served as it is
EMPTY_DAYS = 1                 # "nothing found" is remembered for a day only
STALE_DAYS = 30                # if the search fails, an answer up to this old is still better than nothing
KEEP, SHOW = 5, 2              # stored per (pincode, doctor), and shown to the patient
MAX_KM = 30                    # farther than this from the pincode's centre is not "near"
REVIEW_CHARS, MAX_REVIEWS = 600, 20   # per place we keep up to 20 reviews, text only, this long
RATING_FLOORS = (20, 10, 5)    # minimum number of ratings; relaxed step by step only if fewer than 2 listings pass

# What we type into the search box, per doctor in doctors.py. Verified on real data for the first six only.
SEARCH_PHRASE = {
    "general_physician": "general physician", "paediatrician": "paediatrician", "gynaecologist": "gynaecologist",
    "ent": "ENT specialist", "dentist": "dentist", "dermatologist": "dermatologist",
    "ophthalmologist": "ophthalmologist", "orthopaedist": "orthopaedic doctor", "gastroenterologist": "gastroenterologist",
    "cardiologist": "cardiologist", "pulmonologist": "pulmonologist", "neurologist": "neurologist",
    "endocrinologist": "endocrinologist", "urologist": "urologist", "nephrologist": "nephrologist",
    "psychiatrist": "psychiatrist", "general_surgeon": "general surgeon", "neonatologist": "neonatologist",
    "rheumatologist": "rheumatologist", "haematologist": "haematologist", "oncologist": "oncologist",
    "neurosurgeon": "neurosurgeon", "cardiac_surgeon": "cardiac surgeon", "plastic_surgeon": "plastic surgeon",
    "paediatric_surgeon": "paediatric surgeon", "rehab_doctor": "physical medicine and rehabilitation doctor",
}

# A name with "Dr" in it is a doctor, whatever hospital or clinic they work at. Without it, the name decides nothing good:
# a listing whose own category is generic ("Medical clinic", "Doctor", "Medical Center"...) could be anything, so it is dropped.
DR = re.compile(r"\bdrs?\b", re.I)
GENERIC_TYPE = {"doctor", "medical clinic", "medical center", "health consultant", "consultant", "clinic", "specialized clinic", "medical group"}
# Not a clinic: hospitals, wards, pharmacies and labs. Judged by the listing's own category; the name counts too unless it has "Dr"
# in it ("Dr X | Manipal Hospital" is a doctor who works at one). AYUSH is a different kind of doctor from the ones we suggest (any label).
NOT_CLINICS = re.compile(r"hospital|\bwards?\b|nursing home|pharmac|laborator|diagnostic|\bmedicals\b|chemist", re.I)
AYUSH = re.compile(r"ayush|ayurved|homeo|unani|siddha|naturopath|acupunct", re.I)
# A listing whose main category is a beauty or wellness service, not a clinic
NOT_DOCTORS = re.compile(r"tattoo|hair removal|piercing|esthetic|whitening|weight loss|\bsalon|\bspa\b|\bgym|yoga", re.I)
# Labels Google puts on specialists. A "general physician" search skips listings that carry one of these.
SPECIALIST = re.compile(
    r"cardiolog|otolaryngolog|gyn[a]?ecolog|obstetric|p[a]?ediatric|dermatolog|orthop[a]?edic|psychiatr|neurolog|urolog|endocrinolog|"
    r"nephrolog|gastroenterolog|pulmonolog|ophthalmolog|oncolog|rheumatolog|haematolog|hematolog|pain management|"
    r"surgeon|dentist|dental|neonatal|fertility", re.I)
# For the five specialists verified on real data, a listing whose own category is specific must be that specialty.
MUST_BE = {
    "paediatrician": re.compile(r"p[a]?ediatr|neonat|child", re.I),
    "gynaecologist": re.compile(r"gyn[a]?ecolog|obstetric|women|maternity|fertility|pregnan", re.I),
    "ent": re.compile(r"otolaryng|\bent\b|ear,? nose|throat", re.I),
    "dentist": re.compile(r"dent|orthodont|endodont|periodont|oral", re.I),
    "dermatologist": re.compile(r"dermat|skin|trichol|hair", re.I),
}
# For a general physician, a specific category must be one of these (a cardiologist is not a general physician)
GP_TYPES = {"general practitioner", "family practice physician", "physician", "internist"}


class NotConfigured(Exception):
    """No SERPER_API_KEY on the server."""


class ProviderError(Exception):
    """The search failed (network, a bad answer, a refused key). Never cached."""


# ---------- the provider (the only Serper-specific code) ----------

def _request(url: str, body: dict, key: str) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={"X-API-KEY": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=SERPER_TIMEOUT) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise ProviderError(f"Serper answered HTTP {e.code}") from None   # never include the request, it carries the key
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise ProviderError(f"Serper search failed: {type(e).__name__}") from None
    if not isinstance(data, dict) or data.get("error") or data.get("message"):
        raise ProviderError("Serper sent an unusable answer")
    return data


def _post(phrase: str, pincode: str, key: str) -> dict:
    data = _request(SERPER_URL, {"q": f"{phrase} near {pincode}", "gl": "in", "hl": "en"}, key)
    places = data.get("places")
    if places is None and "searchParameters" in data:
        places = []                       # a valid answer with no listings
    if not isinstance(places, list):
        raise ProviderError("Serper sent no places list")
    return {"places": places}


def _post_reviews(place_id: str, key: str) -> list:
    data = _request(SERPER_REVIEWS_URL, {"placeId": place_id, "gl": "in", "hl": "en"}, key)
    found = data.get("reviews")
    if found is None and "searchParameters" in data:
        found = []                        # a place with no reviews
    if not isinstance(found, list):
        raise ProviderError("Serper sent no reviews list")
    return found


def _number(v, cast):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


async def serper_search(pincode: str, doctor_key: str) -> list:
    """The raw places for 'doctor near pincode', in our plain shape:
    {title, address, lat, lng, rating, count, phone, type, types}. Raises NotConfigured or ProviderError."""
    key = os.environ.get("SERPER_API_KEY", "").strip()
    if not key:
        raise NotConfigured()
    data = await asyncio.to_thread(_post, SEARCH_PHRASE[doctor_key], pincode, key)
    return plain(data["places"])


async def serper_reviews(place_id: str) -> list:
    """Up to MAX_REVIEWS reviews of one place as [{r: stars, t: text}]: text and stars only, never the reviewer's name, photo or link.
    Raises NotConfigured or ProviderError. (1 credit.)"""
    key = os.environ.get("SERPER_API_KEY", "").strip()
    if not key:
        raise NotConfigured()
    return plain_reviews(await asyncio.to_thread(_post_reviews, place_id, key))


def plain_reviews(raw) -> list:
    out = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        text = " ".join(str(r.get("snippet") or "").replace("\u2b50", " ").split())[:REVIEW_CHARS]   # the snippet starts with star emoji
        if text:
            out.append({"r": _number(r.get("rating"), float), "t": text})
    return out[:MAX_REVIEWS]


def plain(raw_places: list) -> list:
    """Serper's places in our plain shape."""
    out = []
    for p in raw_places:
        if not isinstance(p, dict) or not str(p.get("title") or "").strip():
            continue
        main = str(p.get("type") or "")
        out.append({
            "title": str(p["title"]).strip(), "address": str(p.get("address") or "").strip(),
            "lat": _number(p.get("latitude"), float), "lng": _number(p.get("longitude"), float),
            "rating": _number(p.get("rating"), float), "count": _number(p.get("ratingCount"), int),
            "phone": str(p.get("phoneNumber") or ""), "type": main, "id": str(p.get("placeId") or ""),
            "types": [str(t) for t in p.get("types") or [] if t] or ([main] if main else []),
        })
    return out


# ---------- the rules ----------

def km_between(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def clean_name(title: str) -> str:
    """The doctor's or clinic's name without the advertising that Google listings often carry."""
    t = re.split(r"\s\|\s", title)[0]
    t = re.sub(r"\s*[,-]\s*(treated|best|top)\b.*$", "", t, flags=re.I)
    t = re.sub(r"\s+-\s+(best\s+)?[\w\s.]+?\s+in\s+[\w\s]+$", "", t, flags=re.I)   # "Dr X - Pediatrician in Malviya Nagar"
    t = re.sub(r"^(best|top)\s+[\w\s]+?\s*[-:]\s*", "", t, flags=re.I)
    return " ".join(t.strip(" -,").split()) or title


def clean_phone(raw: str) -> str:
    """+91 and ten digits, or nothing if it doesn't look like an Indian number."""
    d = re.sub(r"\D", "", raw or "")
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    return "+91" + d if len(d) == 10 else ""


def in_india(p) -> bool:
    return p["lat"] is not None and p["lng"] is not None and 6 <= p["lat"] <= 38 and 68 <= p["lng"] <= 98   # also drops listings from other countries


def is_candidate(p, doctor_key: str) -> bool:
    has_dr = bool(DR.search(p["title"]))
    if NOT_CLINICS.search(p["type"]) or (not has_dr and NOT_CLINICS.search(p["title"])) or NOT_DOCTORS.search(p["type"]):
        return False
    if AYUSH.search(" ".join([p["title"], *p["types"]])):
        return False
    if doctor_key == "general_physician" and any(SPECIALIST.search(t) for t in p["types"]):
        return False
    if p["type"].lower() in GENERIC_TYPE:
        if not has_dr:          # "Medical clinic", "Doctor"... with no Dr in the name: could be anything
            return False
    else:                       # a specific category (Gastroenterologist, Dental clinic...) has to fit the doctor we searched for
        if doctor_key == "general_physician" and p["type"].lower() not in GP_TYPES:
            return False
        must = MUST_BE.get(doctor_key)
        if must and not must.search(" ".join(p["types"])):
            return False
    return bool(p["rating"]) and bool(p["count"])


def choose(places: list, pincode: str, doctor_key: str) -> list:
    """The best KEEP listings for this pincode and doctor, best first. Each is {name, rating, count, km, address, phone, lat, lng, score, id}."""
    here = [p for p in places if in_india(p)]
    if not here:
        return []
    near = [p for p in here if pincode in p["address"]] or here
    centre = (statistics.median(p["lat"] for p in near), statistics.median(p["lng"] for p in near))   # our own centre: Serper's can be wrong
    cur = [p for p in here if is_candidate(p, doctor_key) and km_between(centre, (p["lat"], p["lng"])) <= MAX_KM]
    keep = []
    for floor in RATING_FLOORS:
        keep = [p for p in cur if p["count"] >= floor]
        if len(keep) >= 2:
            break
    by_phone = defaultdict(list)          # listings of one clinic share a phone number: keep the one with the most ratings
    for i, p in enumerate(keep):
        by_phone[clean_phone(p["phone"]) or f"none-{i}"].append(p)
    keep = [max(g, key=lambda p: p["count"]) for g in by_phone.values()]
    if not keep:
        return []
    mean = statistics.mean(p["rating"] for p in keep)

    def score(p):   # reviews only: the star rating, pulled toward the average when it rests on few ratings
        w = p["count"] / (p["count"] + 50)
        return w * p["rating"] + (1 - w) * mean

    out = []
    for p in sorted(keep, key=lambda p: (score(p), p["count"]), reverse=True)[:KEEP]:
        out.append({
            "name": clean_name(p["title"])[:100], "rating": round(p["rating"], 1), "count": p["count"],
            "km": round(km_between(centre, (p["lat"], p["lng"])), 1), "address": re.sub(r",?\s*India$", "", p["address"])[:200],
            "phone": clean_phone(p["phone"]), "lat": round(p["lat"], 6), "lng": round(p["lng"], 6),
            "score": round(score(p), 3), "id": p.get("id", ""),   # the star score (the review bonus is added on top later) and the id to fetch reviews with
        })
    return out


# ---------- the cache and the single-flight ----------

_inflight: dict = {}   # (pincode, doctor_key) -> the one running search; fine for one server process (Render free runs one)


async def _read(db_run, pincode, key):
    """(results, age in days) of the stored answer, or None. A cache problem is logged and treated as 'nothing stored'."""
    try:
        row = await db_run(
            "fetchrow",
            "select results::text as results, extract(epoch from now() - fetched_at) / 86400 as age "
            "from nearby_cache where pincode = $1 and doctor_type = $2", pincode, key,
        )
    except Exception as e:
        log.warning("nearby cache read failed: %r", e)
        return None
    if row is None:
        return None
    try:
        results = json.loads(row["results"])
    except ValueError:
        return None
    return (results, float(row["age"])) if isinstance(results, list) else None


async def _write(db_run, pincode, key, results):
    try:
        await db_run(
            "execute",
            "insert into nearby_cache (pincode, doctor_type, results, fetched_at) values ($1, $2, $3::jsonb, now()) "
            "on conflict (pincode, doctor_type) do update set results = excluded.results, fetched_at = now()",
            pincode, key, json.dumps(results, ensure_ascii=False),
        )
    except Exception as e:   # the patient still gets the answer we just fetched
        log.warning("nearby cache write failed: %r", e)


def _complete(results) -> bool:
    """A stored answer is usable as fresh only if every place carries its reviews (rows written before reviews existed are not)."""
    return all(isinstance(p, dict) and isinstance(p.get("reviews"), list) for p in results)


async def _attach_reviews(chosen: list, fetch) -> list:
    """Read the reviews of each chosen place at the same time. A place whose reviews can't be fetched gets None (the search still works)."""
    async def one(p):
        if not p.get("id"):
            return []                    # nothing to look up
        try:
            return await fetch(p["id"])
        except Exception as e:           # never let a review problem fail the search
            log.warning("reviews for %r not fetched: %s", p["name"], e if isinstance(e, (ProviderError, NotConfigured)) else repr(e))
            return None
    got = await asyncio.gather(*[one(p) for p in chosen])
    return [{**{k: v for k, v in p.items() if k != "id"}, "reviews": r} for p, r in zip(chosen, got)]


async def _work(db_run, pincode, key, search, fetch_reviews):
    k = (pincode, key)
    try:
        stored = await _read(db_run, pincode, key)   # look again: the search we queued behind may just have filled it
        if stored:
            results, age = stored
            if age < (FRESH_DAYS if results else EMPTY_DAYS) and _complete(results):
                return {"results": results, "source": "cache"}
        try:
            places = await search(pincode, key)
        except ProviderError as e:
            if stored and stored[0] and stored[1] < STALE_DAYS:
                log.warning("nearby search failed (%s); serving the %.0f-day-old stored answer", e, stored[1])
                return {"results": stored[0], "source": "stale"}
            raise
        results = await _attach_reviews(choose(places, pincode, key), fetch_reviews)
        if all(p["reviews"] is not None for p in results):
            await _write(db_run, pincode, key, results)
        else:
            log.warning("nearby %s %s: some reviews missing, answer not cached", key, pincode)
        log.info("nearby %s %s: %d places from the provider, %d kept, %d reviews read", key, pincode, len(places), len(results),
                 sum(len(p["reviews"] or []) for p in results))
        return {"results": results, "source": "live"}
    finally:
        _inflight.pop(k, None)   # only after the answer is stored, so the next press finds it in the cache


async def _terms(terms_for, conditions):
    """Search terms for each condition, written while the search runs. If it fails the names' own words are used (never an error)."""
    try:
        got = await terms_for([c["name"] for c in conditions])
        if isinstance(got, list) and len(got) == len(conditions):
            return got
    except Exception as e:
        log.warning("review terms not written: %r", e)
    return [[] for _ in conditions]


SHOWN = ("name", "rating", "count", "km", "address", "phone", "lat", "lng")   # what leaves the server; not the reviews, scores or ids


async def find_nearby(db_run, pincode: str, doctor_key: str, search=None, reviews=None, conditions=None, terms_for=None) -> dict:
    """{results: [up to SHOW listings], source: 'live' | 'cache' | 'stale'}. Raises NotConfigured or ProviderError.
    Presses for the same (pincode, doctor) while a search is running all wait for that one search (and its one batch of reviews).
    With `conditions` ([{tier, name}] from the assessment) the places are re-ordered by their reviews, and each comes with a 'match' summary."""
    search, reviews = search or serper_search, reviews or serper_reviews   # looked up now, so tests can swap them
    terms_task = asyncio.create_task(_terms(terms_for, conditions)) if conditions and terms_for else None
    try:
        k = (pincode, doctor_key)
        task = _inflight.get(k)
        if task is None:   # no await between this check and the next line: two presses can't both start a search
            task = asyncio.create_task(_work(db_run, pincode, doctor_key, search, reviews))
            _inflight[k] = task
            task.add_done_callback(lambda t: t.cancelled() or t.exception())   # mark a failure as seen, so nobody's cancelled wait leaves a stray log
        out = await asyncio.shield(task)   # one press leaving (closed tab) doesn't stop the search for the others
    except BaseException:
        if terms_task:
            terms_task.cancel()
        raise
    results = out["results"]
    if conditions:
        terms = await terms_task if terms_task else [[] for _ in conditions]
        results = rv.rank(results, conditions, terms)
    shown = [{**{f: p[f] for f in SHOWN if f in p}, **({"match": p["match"]} if "match" in p else {})} for p in results[:SHOW]]
    return {"results": shown, "source": out["source"]}
