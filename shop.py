"""Things that may help: simple self-care items (ORS, a heating pad, a thermometer...) shown as example listings near the patient's pincode.

How it works:
  ITEMS          a small, fixed list of product TYPES (never brands, never medicines). Edit it here; it needs a clinician's review before real use.
                 Each item has plain-words text (`use` is what the small model reads, with "NOT for" limits), one search phrase, a must-match and a block rule for listing titles, a price limit, age limits, and optionally `only_if`.
  main.py        a small model picks up to 3 KEYS from this list for the patient (it never writes a search); age_ok() then removes unsuitable ones.
  serper_products  the ONLY provider-specific code: Google Shopping through Serper.dev (2 credits a search).
  choose_products  keeps listings from known shops (quick-commerce apps first, then pharmacies, then online stores), with an image and a price,
                 whose title fits the item; at most 2 per shop, 6 in all.
  find_products  the cache (table nearby_cache, key "shop:<item>"; fresh 1 day) plus single-flight, so a double press makes one search per item.
Each listing links to its Google Shopping page (where the person picks a shop), not to a shop's own page: no shop offers us a public catalog API.
Images are Google's own thumbnails, shown straight from Google. NOT clinically reviewed; example listings, not recommendations.
"""
import asyncio
import logging
import os
import re
from urllib.parse import quote

import places

log = logging.getLogger("forlife")

SHOPPING_URL = "https://google.serper.dev/shopping"
FRESH_DAYS, EMPTY_DAYS, STALE_DAYS = 1, 0.1, 7   # prices and stock move: a day; "nothing found" about 2 hours; an older copy only if the search is down
MAX_PRODUCTS, PER_SHOP, MAX_ITEMS = 6, 2, 3
YEAR = 12   # months

ITEMS = {
    # ---------- existing 8, corrected ----------
    "ors": {
        "max_price": 400,
        "name": "Oral rehydration salts (ORS)",
        "use": "loose motions (diarrhoea) or vomiting from a stomach bug or food poisoning, heat exhaustion with heavy sweating, or drinking "
               "too little because of a sore mouth or throat (for example hand, foot and mouth disease), to prevent or treat mild dehydration. Not a treatment for blood in the stool, a baby or child who will not drink or keeps vomiting "
               "everything, or heatstroke (these need a doctor).",
        "why": "Replaces the water and salts lost with loose motions, vomiting or heavy sweating. Small sips, often.",
        "note": "Mix with exactly the amount of clean water the pack says (too little water is harmful); for babies, ask your doctor.",
        "query": "ORS powder sachet",
        "must": r"^(?=.*(\bo\.?\s?r\.?\s?s\b|oral rehydration))(?=.*(sachet|powder|\d\s*(g|gm)\b))",
        "block": r"\borsl\b|fast\s*&?\s*up|reload|effervescent|energy|sport|gatorade|glucon|drink|juice|tetra|ready to drink|\d\s*ml\b|zinc|tablet|"
                 r"electrolyte water|hydration mix|protein",
        "min_months": 0, "max_months": None,
    },
    "heating_pad": {
        "max_price": 3000,
        "name": "Heating pad",
        "use": "period cramps, or stiff sore muscles in the back, neck or shoulders (including a tension headache from tight neck muscles), "
               "after the first 2 days. NOT for belly or stomach pain other than period cramps, a new injury or swelling in its first 2 days, "
               "tooth, jaw or face pain or swelling, or anyone with numb skin (for example from diabetes).",
        "why": "Gentle warmth can ease period cramps and stiff, sore muscles.",
        "note": "Low setting, over clothing; never sleep with it on or use it on numb skin.",
        "query": "heating pad for period cramps",
        "must": r"heat|warm",
        "block": r"infrared lamp|\bice\b|water bag|hot bag|heating bag|\bgel\b|patch|slimming|weight loss|belly fat|sauna",
        "min_months": 12 * YEAR, "max_months": None,
    },
    "hot_water_bag": {
        "max_price": 1500,
        "name": "Hot water bag",
        "use": "period cramps, or stiff sore muscles in the back, neck or shoulders, after the first 2 days. NOT for belly or stomach pain "
               "other than period cramps, a new injury or swelling in its first 2 days, tooth, jaw or face pain or swelling, or anyone with "
               "numb skin (for example from diabetes).",
        "why": "A warm (not boiling) water bag can ease cramps and aches.",
        "note": "Never use boiling water; wrap it in a cloth and don't sleep on it.",
        "query": "hot water bag",
        "must": r"hot.?water (bag|bottle)",
        "block": r"\bice\b|cold|cooler|thermos|flask|vacuum|steel|sipper|insulated|(?<!non )(?<!non-)\belectric|electro|rechargeable|charging|\bgel\b|automatic",
        "min_months": 12 * YEAR, "max_months": None,
    },
    "cold_pack": {
        "max_price": 1500,
        "name": "Cold pack",
        "use": "swelling or pain in the first 2 days after a knock, bruise, or mild sprain or strain; a swollen itchy insect bite; heel pain; sore piles; "
               "or to help cool someone with heat exhaustion (under the armpits or on the neck). NOT for snake or animal bites, burns, "
               "numb skin, or a suspected broken bone instead of seeing a doctor.",
        "why": "Cold can ease swelling and pain after a knock or a sprain, and soothe a swollen bite.",
        "note": "Wrap it in a cloth and use it for up to 20 minutes at a time, never straight on the skin.",
        "query": "instant cold pack ice pack for injury",
        "must": r"cold|\bice\b|cool",
        "block": r"cooler|lunch|thermos|insulated|bottle|flask|vacuum|beverage|eye mask|face|roller|beauty|towel|cap\b|vest",
        "min_months": 36, "max_months": None,
    },
    "thermometer": {
        "max_price": 1500,
        "name": "Digital thermometer",
        "use": "fever, or a viral illness where temperature should be tracked (also dengue, malaria or typhoid being watched by a doctor).",
        "why": "Lets you check a fever properly and see whether it is coming down.",
        "note": "A fever in a baby under 3 months needs a doctor the same day.",
        "query": "digital thermometer",
        "must": r"thermometer",
        "block": r"room|fridge|aquarium|food|kitchen|meat|cooking|pool|bath|incubator|infrared|non.?contact|forehead|\bgun\b|laser|hygrometer|"
                 r"humidity|sensor|probe|wired|environment|indoor|outdoor|weather|clock|mercury|glass|candy|\boil\b|\blab\b|industrial",
        "min_months": 0, "max_months": None,
    },
    "saline_nasal": {
        "max_price": 400,
        "name": "Saline nasal drops",
        "use": "a blocked or stuffy nose from a cold or allergy (including allergic or non-allergic rhinitis, and blocked ears from a cold).",
        "why": "Salt-water drops loosen a blocked nose and have no medicine in them.",
        "note": "For babies, choose drops made for infants.",
        "query": "saline nasal drops",
        "must": r"saline|sodium chloride|isotonic",
        "block": r"oxymetazoline|xylometazoline|otrivin|nasivion|decongest|medicated|vicks|sinarest|fluticasone|budesonide|mometasone|steroid|"
                 r"azelastine|\boil\b|almond|ghee|taila|ayur|herbal|menthol|eucalyptus|camphor|homeo|hypertonic|aspirator|rinse|wash",
        "min_months": 0, "max_months": None,
    },
    "nasal_aspirator": {
        "max_price": 1200,
        "name": "Baby nose cleaner (nasal aspirator)",
        "use": "a baby's or toddler's blocked nose from a cold, when it makes feeding or sleep hard.",
        "why": "Gently clears mucus from a baby's blocked nose, which helps feeding and sleep.",
        "note": "Be gentle, only at the nostril's opening, a few times a day at most.",
        "query": "baby nasal aspirator",
        "must": r"aspirator|nose|nasal|snot",
        "block": r"adult|vacuum cleaner|\bear\b|syringe|electric|battery|rechargeable|hair|trimmer",
        "min_months": 0, "max_months": 36,
    },
    "elastic_bandage": {
        "max_price": 700,
        "name": "Elastic (crepe) bandage",
        "use": "light support for a mildly sprained or strained ankle, knee or wrist. NOT for the shoulder, a suspected broken bone (heard a "
               "crack, odd shape, can't bear weight, numbness), or a swollen painful calf.",
        "why": "Gives light support to a mildly sprained or strained joint during the day.",
        "note": "Snug, not tight, and off at night. Loosen it if fingers or toes turn pale, cold or numb.",
        "query": "crepe bandage elastic",
        "must": r"bandage|crepe",
        "block": r"first aid kit|plaster|tape|adhesive|cohesive|kinesio|self.?adher|gauze|roll of cotton",
        "min_months": 36, "max_months": None,
    },

    # ---------- new ----------
    "toilet_footstool": {
        "max_price": 1500,
        "name": "Toilet footstool",
        "use": "constipation, hard stools, anal fissure or piles: resting the feet on a low stool raises the knees above the hips and makes "
               "passing stool easier. NOT a fix for constipation with severe belly pain, a swollen belly, vomiting, black stools or heavy bleeding.",
        "why": "Resting your feet on a low stool on the toilet can make it easier to pass stool without straining.",
        "note": "",
        "query": "toilet foot stool for constipation",
        "must": r"^(?=.*\b(toilet|potty|squat\w*|bathroom)\b)(?=.*\b(stool|step|foot ?rest)\b)",
        "block": r"training seat|potty seat|seat cover|ladder|commode|chair|wipe|cleaner|brush|bidet|tissue|urinal|plunger|kitchen|rack|shelf|"
                 r"organi[sz]er|camping|portable toilet|\bpan\b",
        "min_months": 36, "max_months": None,
    },
    "sitz_bath": {
        "max_price": 1200,
        "name": "Sitz bath tub",
        "use": "piles (haemorrhoids) or a small tear at the anus (anal fissure): sitting in a few inches of warm plain water eases pain and "
               "itching. NOT for heavy bleeding, black stools, a painful lump with fever, or children who can't sit safely on the toilet.",
        "why": "A small tub that sits on the toilet so you can soak your bottom in warm water, which can ease pain and itching.",
        "note": "Warm, not hot, plain water for 10 to 15 minutes; nothing added.",
        "query": "sitz bath tub for piles",
        "must": r"sitz",
        "block": r"\bsalts?\b|powder|herbal|ayur|epsom|povidone|betadine|medicated|solution|\bwash\b|cream|\boil\b|tablet",
        "min_months": 12 * YEAR, "max_months": None,
    },
    "nasal_rinse": {
        "max_price": 1000,
        "name": "Nasal rinse bottle (salt-water wash)",
        "use": "allergic rhinitis (allergic nose), non-allergic rhinitis, or a long-lasting blocked or runny nose from sinus trouble: rinsing "
               "the nose with salt water washes out mucus and allergens. NOT for nosebleeds, a recent nose or sinus operation, a sinus "
               "infection with fever, facial swelling or eye swelling, or an ear infection.",
        "why": "Rinsing the nose with salt water can wash out mucus and dust or pollen, which can ease a stuffy, runny nose.",
        "note": "Use only boiled-and-cooled or sterile water, never tap water; clean and dry the bottle after each use.",
        "query": "nasal rinse bottle saline sinus wash",
        "must": r"^(?=.*(nasal|nose|sinus|neti))(?=.*(rinse|wash|irrigat|douche|neti))",
        "block": r"spray|drops|\boil\b|ayur|herbal|menthol|eucalyptus|aspirator|baby|kids|infant|electric|pulsat|steam|inhal|vapou?r|"
                 r"refill|sachets? only|cleaner for",
        "min_months": 12 * YEAR, "max_months": None,
    },
    "eye_mask": {
        "max_price": 600,
        "name": "Sleep eye mask",
        "use": "poor sleep or insomnia where light disturbs sleep, or a migraine where lying down in the dark helps. NOT for eye pain, a red "
               "or swollen eye, an eye injury, or eye discharge.",
        "why": "Keeps light out so it is easier to fall asleep, or to rest in the dark during a migraine.",
        "note": "",
        "query": "sleep eye mask",
        "must": r"sleep(ing)? (eye )?mask|eye mask|blindfold|eye ?shade|eye cover",
        "block": r"under.?eye|patch|\bgel\b|serum|cream|sheet|hydrogel|collagen|peel|steam|heated|warm|usb|electric|bluetooth|headphone|music|"
                 r"speaker|massager|beauty|vitamin|cucumber|lavender|herbal|kids|cartoon",
        "min_months": 12 * YEAR, "max_months": None,
    },
    "ear_plugs": {
        "max_price": 500,
        "name": "Foam earplugs for sleep",
        "use": "poor sleep or insomnia where noise wakes the person. NOT for any ear problem: ear pain, discharge, blocked or muffled ears, "
               "fluid behind the eardrum, an ear infection, or ringing in the ears.",
        "why": "Softens noise around you so it is easier to fall and stay asleep.",
        "note": "Don't use with ear pain or discharge; keep them away from small children.",
        "query": "foam ear plugs for sleeping",
        "must": r"ear ?plugs?",
        "block": r"earphone|headphone|earbud|\bbuds?\b|bluetooth|wireless|\btws\b|wax (remov|clean)|cleaner|remover|drops|\boil\b|"
                 r"hearing aid|candle|kids|child",
        "min_months": 12 * YEAR, "max_months": None,
    },
    "lice_comb": {
        "max_price": 600,
        "name": "Fine-toothed lice comb",
        "use": "head lice or nits: combing wet, conditioned hair with a fine-toothed comb removes lice without any medicine. NOT for "
               "scalp sores, pus, a spreading rash, or dandruff and other scalp conditions.",
        "why": "Combing wet hair with a fine-toothed comb removes lice and eggs without any medicine or chemicals.",
        "note": "Comb wet, conditioned hair from the roots on days 1, 5, 9 and 13, then check again on day 17.",
        "query": "head lice comb fine tooth",
        "must": r"^(?=.*\b(lice|nits?|louse)\b)(?=.*comb)",
        "block": r"shampoo|\boil\b|lotion|spray|serum|electric|battery|permethrin|\bkit\b|ayur|herbal|neem|tea tree|solution|cream|"
                 r"medik|\bpet\b|\bdogs?\b|\bcats?\b|flea",
        "min_months": 24, "max_months": None,
    },
    "heel_cups": {
        "max_price": 800,
        "name": "Silicone heel cups (heel pads)",
        "use": "heel pain from plantar fasciitis or a heel spur, worse with the first steps in the morning. NOT for heel pain after a fall or "
               "jump, a hot swollen red foot, numb feet or foot sores (for example with diabetes).",
        "why": "Soft heel pads in your shoes cushion the heel and can ease heel pain when you walk.",
        "note": "Wear them in comfortable shoes with a low heel.",
        "query": "silicone heel cups for heel pain",
        "must": r"^(?=.*\bheel)(?=.*\b(cups?|cushions?|pads?|seats?|inserts?|insoles?|spur|plantar))",
        "block": r"sock|crack|moistur|repair|cream|balm|\bfile\b|scrub|grip|liner|high heels?\b|shoe horn|massager|slipper|sandal|"
                 r"footwear|splint|brace|bandage|sleeve|callus|\bcorns?\b",
        "min_months": 12 * YEAR, "max_months": None,
    },
    "wrist_splint": {
        "max_price": 1200,
        "name": "Wrist splint for night use",
        "use": "carpal tunnel syndrome: tingling, numbness or pins and needles in the thumb and fingers, worse at night. Worn at night to keep "
               "the wrist straight. NOT after a fall or injury to the wrist (a broken bone can be missed), and not for sudden weakness.",
        "why": "Keeping the wrist straight at night can ease tingling and numbness in the hand.",
        "note": "Wear it at night; if the hand gets weaker or numbness spreads, see a doctor.",
        "query": "wrist splint for carpal tunnel",
        "must": r"^(?=.*wrist)(?=.*(splint|brace|cock.?up))",
        "block": r"binder|\bband\b|gym|\bweight|lifting|watch|thumb|finger|fracture|\bcast\b|kids|child|heat|magnetic|copper|"
                 r"tourmaline|bracelet|smart",
        "min_months": 18 * YEAR, "max_months": None,
    },
    "bp_monitor": {
        "only_if": r"hypertension|high blood pressure|raised blood pressure",   # shown only when a likely condition name matches this
        "max_price": 3500,
        "name": "Upper-arm blood pressure monitor",
        "use": "ONLY when the likely condition is high blood pressure (hypertension), raised blood pressure found at a check, or known high "
               "blood pressure that needs readings at home for a doctor to review. NOT for headache, migraine, dizziness, chest pain, "
               "palpitations, anxiety, pregnancy, or children.",
        "why": "Home readings, taken the right way, help your doctor see your real blood pressure over several days.",
        "note": "Sit and rest 5 minutes first, cuff on the bare upper arm; show the readings to your doctor and don't change medicines yourself.",
        "query": "upper arm blood pressure monitor",
        "must": r"^(?=.*(blood pressure|\bbp\b))(?=.*(monitor|machine|meter))",
        "block": r"wrist|watch|\bband\b|finger|\bring\b|smart ?band|replacement cuff|cuff only|^cuff\b|adapt[eo]r for|sphygmo|aneroid|"
                 r"manual (bp|blood)|stethoscope|kids|child|paediatric|pediatric",
        "min_months": 18 * YEAR, "max_months": None,
    },
    "petroleum_jelly": {
        "max_price": 400,
        "name": "Plain petroleum jelly",
        "use": "dry, rough or cracked skin, cracked heels, chapped lips, or mild eczema (dry, itchy patches) to seal in moisture. NOT for burns, "
               "weeping, crusted or infected skin, fungal rashes (ringworm, rashes in skin folds or the groin), acne, or a rash with fever.",
        "why": "A thin layer on damp skin seals in moisture and can ease dry, itchy skin.",
        "note": "Keep away from flames: clothes and bedding with it on catch fire easily.",
        "query": "white petroleum jelly",
        "must": r"petroleum jelly|petrolatum|white soft paraffin|skin jelly",
        "block": r"aloe|cocoa|vitamin|rose|\blip|tint|shea|almond|perfum|camphor|menthol|boric|antiseptic|cream|lotion|serum|\boil\b|ayur|"
                 r"herbal|calendula|hair|liquid|\bkg\b|litre|industrial|glow|fairness|brighten|spf",
        "min_months": 1, "max_months": None,
    },
    "soft_toothbrush": {
        "max_price": 300,
        "name": "Extra-soft toothbrush",
        "use": "sore or bleeding gums from plaque (gingivitis), mouth ulcers, or a sore gum around a wisdom tooth, so the mouth can still be "
               "cleaned gently. NOT for a swollen face or jaw, fever, pus, or trouble opening the mouth or swallowing (these need a dentist "
               "soon), and not for bleeding gums with easy bruising or fever.",
        "why": "Lets you keep your teeth and gums clean gently while they are sore.",
        "note": "",
        "query": "extra soft toothbrush",
        "must": r"^(?=.*tooth ?brush)(?=.*(soft|sensitive|gentle))",
        "block": r"electric|battery|sonic|kids|child|baby|toothpaste|paste|\bgel\b|charcoal|whitening|tongue|interdental|floss|holder|"
                 r"\bcover\b|\bcase\b|\bcap\b|denture|travel kit|pack of (6|8|10|12)",
        "min_months": 12 * YEAR, "max_months": None,
    },
    "teether": {
        "max_price": 500,
        "name": "Solid silicone teething ring",
        "use": "teething: a baby who is drooling, chewing and fussy with sore, red gums. NOT for a temperature of 38C or more, loose motions, "
               "a rash on the body, or a baby who is unwell or not feeding: teething does not cause these, so look for another cause.",
        "why": "Chewing on a firm, chilled teething ring can soothe sore gums.",
        "note": "Chill it in the fridge, never the freezer; check for cracks and never tie it round the neck.",
        "query": "silicone teether for baby",
        "must": r"teeth(er|ing)",
        "block": r"\bgel\b|liquid|water|fill|freez|necklace|bracelet|bead|amber|chain|clip|wood|neem|powder|tablet|drops|homeo|syrup|"
                 r"tooth ?brush|feeder|nibbler|pacifier|soother|biscuit|rusk|stick|mitten|glove|battery|vibrat",
        "min_months": 3, "max_months": 24,
    },
    "mosquito_net": {
        "max_price": 2000,
        "name": "Mosquito net for the bed",
        "use": "fever where dengue, malaria or chikungunya is possible (viral fever in mosquito season): sleeping under a net keeps "
               "mosquitoes from biting the patient and passing the infection to the family. Not a treatment: any warning signs still "
               "need a doctor.",
        "why": "If the fever could be from dengue or malaria, sleeping under a net stops mosquitoes biting the patient and passing it on to the family.",
        "note": "Tuck it in under the mattress; use it for daytime naps too.",
        "query": "mosquito net for bed",
        "must": r"mosquito net|machhardani|bed net",
        "block": r"repellent|coil|vapori[sz]er|liquid|refill|racket|\bbat\b|killer|\btrap|spray|cream|patch|\bband\b|machine|lamp|electric|"
                 r"zapper|window|door|magnetic|curtain|roll|screen|baby|infant|newborn|toddler|crib|cradle|mattress|stroller|pram",
        "min_months": 12, "max_months": None,
    },
    "plasters": {
        "max_price": 300,
        "name": "Plain fabric plasters (adhesive strips)",
        "use": "a small clean cut or graze that has stopped bleeding. NOT for any animal bite or scratch (dog, cat, monkey: needs a doctor "
               "the same day for rabies care), burns, deep, gaping or dirty wounds, a cut that won't stop bleeding, or a wound with "
               "spreading redness, pus or fever.",
        "why": "Keeps a small cut or graze clean and covered while it heals.",
        "note": "Wash the cut with clean running water first; change the plaster daily.",
        "query": "fabric adhesive bandage strips",
        "must": r"^(?=.*(plaster|strips?|dressing|bandage))(?=.*(strip|adhesive|washproof|waterproof|fabric|plaster))",
        "block": r"antiseptic|medicated|iodine|povidone|silver|anti.?bacterial|antimicrobial|crepe|\broll\b|tape|kinesio|paris|wall|"
                 r"corn|callus|wart|acne|pimple|nicotine|pain|relief|heat|capsic|diclo|menthol|patch|gauze|surgical|micropore|blister|"
                 r"hydrocolloid|scar|silicone|tattoo|\bkit\b|\beye\b|hair|wax",
        "min_months": 12, "max_months": None,
    },
}


# Shops we are willing to show, by kind. Source names are as Google Shopping gives them (lower-cased, matched by "contains").
INSTANT = {"nykaa now": "Nykaa Now", "zepto": "Zepto", "blinkit": "Blinkit", "instamart": "Instamart", "swiggy": "Instamart", "bigbasket": "BigBasket"}
PHARMACY = {"1mg": "Tata 1mg", "pharmeasy": "PharmEasy", "apollo": "Apollo", "netmeds": "Netmeds", "medplus": "MedPlus", "truemeds": "Truemeds"}
MARKET = {"amazon": "Amazon", "flipkart": "Flipkart", "jiomart": "JioMart", "nykaa": "Nykaa", "firstcry": "FirstCry"}
KINDS = (("instant", INSTANT), ("pharmacy", PHARMACY), ("market", MARKET))   # the order they are shown in
PREGNANCY = re.compile(r"pregnan|conceiv|trimester|miscarri|gestation|hyperemesis|morning sickness|antenatal|post-?partum|lactat|breastfe|ectopic", re.I)


def too_risky(conds: list) -> bool:
    """True when no items should be offered: the most likely or the likely condition needs a doctor today, or any risk is missing or unrecognised.
    A LEAST likely condition may be urgent: that is the usual "rule out a serious cause" line, and the assessment already says when to see a doctor."""
    return any(c["risk"] not in ("low", "moderate") and not (c["tier"] == "least_likely" and c["risk"] == "immediate") for c in conds)


def condition_ok(item: dict, names: list) -> bool:
    """An item with an `only_if` pattern is offered only when one of the conditions' names matches it (a blood pressure monitor: only for high blood pressure)."""
    return not item.get("only_if") or any(re.search(item["only_if"], n, re.I) for n in names)


def age_ok(item: dict, age_months) -> bool:
    """An unknown age only gets items that suit every age."""
    if age_months is None:
        return item["min_months"] == 0 and item["max_months"] is None
    return item["min_months"] <= age_months and (item["max_months"] is None or age_months <= item["max_months"])


def request_body(pincode: str, key: str) -> dict:
    return {"q": ITEMS[key]["query"], "gl": "in", "hl": "en", "location": f"{pincode}, India"}   # the pincode as the location works; app names in the query do not


def merchant(source: str):
    """(kind, display name) for a shop we show, else None."""
    s = (source or "").lower()
    for kind, names in KINDS:
        for frag, label in names.items():
            if frag in s:
                return kind, label
    return None


def price_value(text: str):
    """'₹1,014.71' -> 1014.71; None if it is not a plain rupee price."""
    m = re.fullmatch(r"\s*(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)\s*", str(text or ""), re.I)
    return float(m.group(1).replace(",", "")) if m else None


def plain_products(raw: list) -> list:
    out = []
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict) or not str(p.get("title") or "").strip():
            continue
        out.append({"title": " ".join(str(p["title"]).split()), "source": str(p.get("source") or ""), "price": str(p.get("price") or "").strip(),
                    "image": str(p.get("imageUrl") or ""), "link": str(p.get("link") or ""), "pos": places._number(p.get("position"), int) or 999})
    return out


def listing_link(p: dict) -> str:
    """Google's own product link when Serper gave one, else a Google Shopping search for the title. Always google.com."""
    if p["link"].startswith("https://www.google.com/"):
        return p["link"]
    return "https://www.google.com/search?tbm=shop&gl=in&q=" + quote(p["title"][:120])


def choose_products(products: list, key: str) -> list:
    """Listings worth showing for this item: known shops, with an image and a sensible price, a title that fits, instant-delivery apps first."""
    it = ITEMS[key]; must = re.compile(it["must"], re.I); block = re.compile(it["block"], re.I) if it.get("block") else None
    rank = {k: i for i, (k, _) in enumerate(KINDS)}
    cand, seen = [], set()
    for p in products:
        m = merchant(p["source"])
        price = price_value(p["price"])
        if not m or price is None or price > it["max_price"] or not p["image"].startswith("https://") or not must.search(p["title"]) or (block and block.search(p["title"])):
            continue
        sig = (m[1], p["title"].lower())
        if sig in seen:
            continue
        seen.add(sig)
        cand.append((rank[m[0]], p["pos"], p, m))
    cand.sort(key=lambda c: (c[0], c[1]))
    out, per = [], {}
    for _, _, p, (kind, label) in cand:
        if per.get(label, 0) >= PER_SHOP:
            continue
        per[label] = per.get(label, 0) + 1
        out.append({"title": p["title"][:100], "shop": label, "kind": kind, "price": p["price"][:20], "image": p["image"], "link": listing_link(p)})
        if len(out) == MAX_PRODUCTS:
            break
    return out


async def serper_products(pincode: str, key: str) -> list:
    """Google Shopping listings for this item near the pincode, in our plain shape. Raises NotConfigured or ProviderError. (2 credits.)"""
    api_key = os.environ.get("SERPER_API_KEY", "").strip()
    if not api_key:
        raise places.NotConfigured()
    data = await asyncio.to_thread(places._request, SHOPPING_URL, request_body(pincode, key), api_key)
    found = data.get("shopping")
    if found is None and "searchParameters" in data:
        found = []
    if not isinstance(found, list):
        raise places.ProviderError("Serper sent no shopping list")
    return plain_products(found)


# ---------- the cache (table nearby_cache, key "shop:<item>") and the single-flight ----------

_inflight: dict = {}   # (pincode, item) -> the one running search; fine for one server process


def _valid(results) -> bool:
    return isinstance(results, list) and all(isinstance(p, dict) and p.get("title") and str(p.get("link", "")).startswith("https://www.google.com/") for p in results)


async def _work(db_run, pincode, key, search):
    try:
        stored = await places._read(db_run, pincode, f"shop:{key}")   # look again: the search we queued behind may just have filled it
        if stored and _valid(stored[0]) and stored[1] < (FRESH_DAYS if stored[0] else EMPTY_DAYS):
            return {"products": stored[0], "source": "cache"}
        try:
            products = choose_products(await search(pincode, key), key)
        except places.ProviderError as e:
            if stored and stored[0] and _valid(stored[0]) and stored[1] < STALE_DAYS:
                log.warning("shop search failed (%s); serving the %.1f-day-old stored answer", e, stored[1])
                return {"products": stored[0], "source": "stale"}
            raise
        await places._write(db_run, pincode, f"shop:{key}", products)
        log.info("shop %s %s: %d listings kept", key, pincode, len(products))
        return {"products": products, "source": "live"}
    finally:
        _inflight.pop((pincode, key), None)


def _start(db_run, pincode, key, search):
    task = _inflight.get((pincode, key))
    if task is None:   # no await between this check and the next line: two presses can't both start a search
        task = asyncio.create_task(_work(db_run, pincode, key, search))
        _inflight[(pincode, key)] = task
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
    return task


async def find_products(db_run, pincode: str, keys: list, search=None) -> list:
    """[{key, name, why, note, products, source}] for each item that has listings. The searches run at the same time. Raises NotConfigured or
    ProviderError only if every search failed."""
    search = search or serper_products
    tasks = [(k, _start(db_run, pincode, k, search)) for k in keys if k in ITEMS]
    results = await asyncio.gather(*[asyncio.shield(t) for _, t in tasks], return_exceptions=True)
    out, errors = [], []
    for (k, _), r in zip(tasks, results):
        if isinstance(r, BaseException):
            errors.append(r)
            continue
        if r["products"]:
            it = ITEMS[k]
            out.append({"key": k, "name": it["name"], "why": it["why"], "note": it["note"], "products": r["products"], "source": r["source"]})
    if errors and len(errors) == len(tasks):
        raise errors[0]
    return out
