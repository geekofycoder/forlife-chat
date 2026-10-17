"""Reading reviews: how many of a doctor's Google reviews mention the patient's likely conditions. A boost, never a filter.

How it works (decided on real reviews; see the README):
  1. Each condition from the assessment gets search terms: its medical name, everyday names, the organ word people use ("gastric"),
     spelling variants. Claude writes them from the condition names only (main.py); clean_terms() then drops anything unsafe or too generic.
     Tests and scans ("endoscopy") are not allowed as terms: doctors do them for many conditions, so a review that names one says nothing.
  2. A review "hits" a condition when it contains one of its terms as a whole word (plurals and "gall bladder" / "gallbladder" are fine;
     a one-letter typo is allowed only on long single words, so "melena" can't match someone called "Helena").
  3. Each review counts once, for the most likely condition it mentions. Points = the condition's tier weight (most likely 3, likely 2,
     less likely 1), a bit more when the same review hits several different terms.
  4. The points add up over the reviews and are turned into a bonus that grows with more hits but levels off (MAX_BONUS), so a doctor
     with no hits is not penalised and one lucky review can't outrank a clearly better-rated doctor.
Only reviews' text and stars are used; names, photos and links of reviewers are never kept. Not a medical judgement.
"""
import re
from collections import Counter

TIER_WEIGHT = {"most_likely": 3, "likely": 2, "least_likely": 1}
MORE_TERMS = 0.5               # a review that hits 2 different terms for a condition counts 1.5x, 3 or more terms 2x
MAX_BONUS = 0.6                # about half a star at most
SOFTNESS = 6.0                 # raw points at which the bonus is half of MAX_BONUS (2 reviews on the most likely condition)
FUZZY_MIN = 8                  # a typo is tolerated only on single words at least this long
MAX_TERMS = 12

GENERIC = set("""pain pains doctor doctors treatment treat treated problem problems issue issues stomach medicine medicines surgery
disease diseases infection condition symptoms symptom check checkup consultation hospital clinic irritation inflammation inflamed
stool stools blood bleed food else something small mild severe chronic acute upper lower belly abdomen abdominal""".split())
TESTS = set("endoscopy colonoscopy gastroscopy ultrasound scan scans mri xray ecg biopsy test tests testing sample screening".split())
SPECIALTY = set("""gastro gastroenterology gastroenterologist cardio cardiology cardiologist neuro neurology neurologist ortho orthopaedic
derma dermatology dermatologist uro urology urologist nephro nephrology pulmo pulmonology paed paeds gynae gynec gynecology onco oncology ent""".split())
STOP = set("a an the of or and in on at to from with by for is are was be as it its this that than into over under".split())
SUFFIX = r"(?:s|es|ed|ing)?"


def _words(text: str) -> list:
    return re.findall(r"[a-z]+", text.lower())


def clean_terms(raw, limit: int = MAX_TERMS) -> list:
    """Whatever the term writer returned, as a safe list of lowercase letter-only terms (1 to 3 words, 4 to 30 letters)."""
    out = []
    for t in raw if isinstance(raw, (list, tuple)) else []:
        if not isinstance(t, str):
            continue
        words = _words(t)
        term = " ".join(words)
        if not words or len(words) > 3 or not 4 <= len(term) <= 30 or term in out:
            continue
        if any(w in TESTS or w in SPECIALTY for w in words) or all(w in GENERIC or w in STOP for w in words):
            continue
        out.append(term)
    return out[:limit]


def terms_from_name(name: str) -> list:
    """The fallback when no term writer is available: the distinctive words of the condition's own name ("gastritis")."""
    return clean_terms([w for w in _words(name) if len(w) >= 5 and w not in GENERIC and w not in STOP and w not in SPECIALTY])


def _compile(terms: list):
    pats = []
    for term in terms:
        toks = term.split()
        pats.append((term, re.compile(r"\b" + r"[\s\-]*".join(re.escape(t) for t in toks) + SUFFIX + r"\b", re.I)))
    return pats, [t for t in terms if " " not in t and len(t) >= FUZZY_MIN]


def _within_one_edit(a: str, b: str) -> bool:
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        d = [i for i in range(len(a)) if a[i] != b[i]]
        return len(d) == 1 or (len(d) == 2 and d[1] == d[0] + 1 and a[d[0]] == b[d[1]] and a[d[1]] == b[d[0]])   # one wrong letter, or two swapped
    s, l = (a, b) if len(a) < len(b) else (b, a)
    i = j = 0
    skipped = False
    while i < len(s) and j < len(l):
        if s[i] == l[j]:
            i += 1
            j += 1
        elif skipped:
            return False
        else:
            skipped = True
            j += 1
    return True


def hits_in(text: str, compiled) -> set:
    """The distinct terms of one condition that this review contains."""
    pats, fuzzy = compiled
    found = {term for term, rx in pats if rx.search(text)}
    if fuzzy:
        words = None
        for term in fuzzy:
            if term in found:
                continue
            words = words if words is not None else _words(text)
            if any(abs(len(w) - len(term)) <= 1 and _within_one_edit(w, term) for w in words):
                found.add(term)
    return {t for t in found if not any(u != t and t in u for u in found)}   # "ulcer" inside "peptic ulcer" is one mention, not two


def prepare(conditions: list, terms: list) -> list:
    """[{tier, name}] and a term list for each, as the matcher wants them (conditions with no usable term are left out)."""
    out = []
    for c, t in zip(conditions, terms):
        t = clean_terms(t) or terms_from_name(c["name"])
        if t:
            out.append({"w": TIER_WEIGHT.get(c["tier"], 1), "name": c["name"], "terms": t, "compiled": _compile(t)})
    return out


def match_place(reviews, prepared: list) -> dict:
    """{raw, reviews, of, terms}: raw points, how many reviews hit, how many reviews were read, the most-hit terms."""
    texts = [r["t"] for r in reviews or [] if isinstance(r, dict) and r.get("t")]
    raw, n_hit, seen = 0.0, 0, Counter()
    for text in texts:
        best = None
        for cond in prepared:
            found = hits_in(text, cond["compiled"])
            if found:
                pts = cond["w"] * (1 + MORE_TERMS * min(len(found) - 1, 2))
                if best is None or pts > best[0]:
                    best = (pts, found)
        if best:
            raw += best[0]
            n_hit += 1
            seen.update(best[1])
    return {"raw": raw, "reviews": n_hit, "of": len(texts), "terms": [t for t, _ in seen.most_common(3)]}


def bonus(raw: float) -> float:
    return MAX_BONUS * raw / (raw + SOFTNESS) if raw > 0 else 0.0


def rank(results: list, conditions: list, terms: list) -> list:
    """The stored places (best star score first) re-ordered by star score + review bonus. Each comes back with a 'match' entry."""
    prepared = prepare(conditions, terms)
    scored = []
    for p in results:
        m = match_place(p.get("reviews"), prepared) if prepared else {"raw": 0.0, "reviews": 0, "of": 0, "terms": []}
        base = p.get("score", p["rating"])
        scored.append((base + bonus(m["raw"]), p["count"], p, m))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return [dict(p, match={"reviews": m["reviews"], "of": m["of"], "terms": m["terms"]}) for _, _, p, m in scored]
