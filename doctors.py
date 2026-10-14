"""The doctors we can point a family to, in plain words. Edit the text here, then restart the server.

Each entry has the name shown on the card and a short, friendly line on what that doctor looks after.
Claude only picks the key (the schema in prompts.py allows nothing else) and writes the one-line reason for this patient;
the name and the plain-words description always come from this list, so they are the same every time.
NOT clinically reviewed: a doctor should confirm this list before real use.
"""

DOCTORS = {
    # Everyday
    "general_physician": {
        "name": "General Physician",
        "about": "Your everyday family doctor. A good first stop for fevers, coughs, tummy upsets, aches and most common "
                 "worries, and they will point you to a specialist if one is needed.",
    },
    "paediatrician": {
        "name": "Paediatrician",
        "about": "A children's doctor for babies, kids and teenagers. They look after fevers, coughs, tummy upsets, rashes, "
                 "growth and vaccines, and they know how little bodies differ from grown-ups'.",
    },
    "gynaecologist": {
        "name": "Gynaecologist",
        "about": "A doctor for women's health. They look after periods, pregnancy and delivery, and other private health "
                 "concerns, gently and without hurry.",
    },
    "dermatologist": {
        "name": "Dermatologist",
        "about": "A skin, hair and nail doctor. They look after rashes, itching, acne, fungal infections, hair fall and "
                 "skin allergies.",
    },
    "ent": {
        "name": "ENT Specialist",
        "about": "An ear, nose and throat doctor. They look after ear pain, blocked or runny noses, sinus trouble, sore "
                 "throats, tonsils and hearing or voice problems.",
    },
    "ophthalmologist": {
        "name": "Ophthalmologist",
        "about": "An eye doctor. They look after red or painful eyes, eye infections and injuries, blurry vision and "
                 "cataracts.",
    },
    "orthopaedist": {
        "name": "Orthopaedic Doctor",
        "about": "A doctor for bones, joints and muscles. They look after fractures, sprains, back and neck pain, knee "
                 "and joint pain, and sports injuries.",
    },
    "dentist": {
        "name": "Dentist",
        "about": "A doctor for teeth, gums and the mouth. They look after toothache, cavities, swollen gums, loose teeth "
                 "and mouth infections.",
    },
    "gastroenterologist": {
        "name": "Gastroenterologist",
        "about": "A stomach and gut doctor, liver included. They look after long-lasting tummy pain, acidity, vomiting, "
                 "loose motions, constipation and liver problems.",
    },
    "cardiologist": {
        "name": "Cardiologist",
        "about": "A heart doctor. They look after chest pain, a racing or uneven heartbeat, high blood pressure and "
                 "heart check-ups.",
    },
    "pulmonologist": {
        "name": "Pulmonologist",
        "about": "A chest and lung doctor. They look after a cough that won't go, wheezing, asthma, breathlessness and "
                 "chest infections.",
    },
    "neurologist": {
        "name": "Neurologist",
        "about": "A brain and nerves doctor. They look after migraines and severe headaches, fits, dizziness, numbness, "
                 "tingling and weakness.",
    },
    "endocrinologist": {
        "name": "Endocrinologist (Diabetes and Thyroid)",
        "about": "A hormone doctor. They look after diabetes, thyroid problems, and weight or growth concerns linked to "
                 "hormones.",
    },
    "urologist": {
        "name": "Urologist",
        "about": "A doctor for the urinary system and men's reproductive health. They look after urine infections, "
                 "kidney stones, trouble passing urine and prostate concerns.",
    },
    "nephrologist": {
        "name": "Nephrologist",
        "about": "A kidney doctor. They look after kidney disease, swelling from kidney trouble, and blood or protein in "
                 "the urine.",
    },
    "psychiatrist": {
        "name": "Psychiatrist",
        "about": "A doctor for the mind and mood. They look after low mood, anxiety, poor sleep and stress that won't "
                 "ease, kindly and without judgement.",
    },
    "general_surgeon": {
        "name": "General Surgeon",
        "about": "A doctor for problems that may need an operation. They look after hernias, appendix and gall bladder "
                 "trouble, piles, lumps and wounds.",
    },
    # Finer
    "neonatologist": {
        "name": "Neonatologist",
        "about": "A newborn baby specialist. They care for babies in their first weeks, especially those who are small, "
                 "born early or unwell.",
    },
    "rheumatologist": {
        "name": "Rheumatologist",
        "about": "A joint and immune-system doctor. They look after arthritis, long-lasting joint pain and swelling, and "
                 "conditions where the body's own defences act against it.",
    },
    "haematologist": {
        "name": "Haematologist",
        "about": "A blood doctor. They look after anaemia that won't improve, easy bruising or bleeding, and other "
                 "problems with the blood.",
    },
    "oncologist": {
        "name": "Oncologist",
        "about": "A cancer doctor. They check lumps or long-lasting symptoms that may need a cancer check, and plan "
                 "treatment and support if it is needed.",
    },
    "neurosurgeon": {
        "name": "Neurosurgeon",
        "about": "A brain and spine surgeon. They treat head injuries, brain and spine problems and slipped discs that "
                 "may need an operation.",
    },
    "cardiac_surgeon": {
        "name": "Cardiac / Vascular Surgeon",
        "about": "A heart and blood-vessel surgeon. They operate when the heart or blood vessels need surgery, such as "
                 "blocked arteries, valve problems or varicose veins.",
    },
    "plastic_surgeon": {
        "name": "Plastic and Reconstructive Surgeon",
        "about": "A surgeon who repairs and rebuilds. They look after burns, deep wounds and scars, hand injuries and "
                 "repair after an injury or an operation.",
    },
    "paediatric_surgeon": {
        "name": "Paediatric Surgeon",
        "about": "A children's surgeon. They treat babies and children who need an operation, such as a hernia or a "
                 "problem a child was born with.",
    },
    "rehab_doctor": {
        "name": "Rehabilitation Doctor (PM&R)",
        "about": "A doctor who helps people get moving again after an injury, stroke, operation or long-term pain. They "
                 "plan the exercises and therapy, often together with physiotherapists.",
    },
}


# The family's OWN doctors (saved on their profiles). What the (i) shows under that doctor's name and number.
OWN_ABOUT = {
    "family": "Your family doctor already knows you and your health story. They're the best first stop for everyday "
              "illness, can look after the whole family, and will guide you to the right specialist when it's needed.",
    "paediatrician": "Your child's own doctor, who knows how they've grown and what they've had before. The right first "
                     "stop for fevers, coughs, tummy troubles, rashes and vaccines.",
    "gynaecologist": "Your own doctor for women's health (periods, pregnancy and private concerns), who already knows "
                     "your history, so you don't have to start from the beginning.",
}
OWN_WORDS = {"family": "family doctor", "paediatrician": "paediatrician", "gynaecologist": "gynaecologist"}
OWN_ROLE_FOR = {"general_physician": "family", "paediatrician": "paediatrician", "gynaecologist": "gynaecologist"}


def _own(d: dict, role: str, whose: str) -> dict:
    return {"role": role, "label": f"{whose} {OWN_WORDS[role]}", "name": d["name"], "phone": d.get("phone", ""),
            "address": d.get("address", ""), "about": OWN_ABOUT[role]}


def with_own(card, member, own_doctors: list, emergency: bool = False):
    """Put the patient's own doctor on the card. If the doctor we chose is a kind they already have (family doctor,
    paediatrician, gynaecologist), their doctor takes its place. If we chose any other specialist, their own doctor
    is added as 'can also guide you' (their paediatrician for a child, else their family doctor), but not in an
    emergency, where the emergency message must stay the only instruction."""
    if not card or not member or not own_doctors:
        return card
    mine = {d["role"]: d for d in own_doctors}
    whose = "your" if member["relation"] == "self" else f"{member['name'].split()[0]}'s"
    role = OWN_ROLE_FOR.get(card["specialty"])
    if role in mine:
        card["own"] = _own(mine[role], role, whose)
    elif card["specialty"] != "general_physician" and not emergency:
        guide = next((r for r in ("paediatrician", "family") if r in mine and r != role), None)
        if guide:
            card["guide"] = _own(mine[guide], guide, whose)
    return card


def _key(value) -> str:
    return str(value or "").strip().lower()  # the API doesn't guarantee the capitalisation of enum values


def _text(value) -> str:
    return " ".join(str(value or "").split())


def card(raw):
    """The doctor card the page shows: the first doctor (name and plain-words description from the list above, plus
    Claude's one-line reason) and, only when the first is the General Physician, the specialist to see if it doesn't
    settle, with the signs. None if Claude's first pick isn't on the list."""
    if not isinstance(raw, dict):
        return None
    first = _key(raw.get("first") or raw.get("specialty"))  # 'specialty' was the field's name before 'first'
    if first not in DOCTORS:
        return None
    then = _key(raw.get("then"))
    nxt = None
    if first == "general_physician" and then in DOCTORS and then != first:  # a specialist first never gets a second doctor
        nxt = {"specialty": then, "name": DOCTORS[then]["name"], "about": DOCTORS[then]["about"], "when": _text(raw.get("then_when"))}
    return {
        "specialty": first,
        "name": DOCTORS[first]["name"],
        "about": DOCTORS[first]["about"],
        "why": _text(raw.get("why")),
        "then": nxt,
    }
