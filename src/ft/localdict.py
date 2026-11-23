"""Local merchant dictionary: free, offline categorisation. Nothing leaves your machine.

Edit RULES to teach it new merchants: (keyword, category, confidence). Matching rules:
* Runs on the cleaned description (upper case, punctuation -> spaces), whole words only.
  A trailing * on a keyword means "starts with" (e.g. 'SAINSBURY*' matches SAINSBURYS).
* The LONGEST matching keyword wins, so 'AMAZON PRIME' beats 'AMAZON' and 'UBER EATS' beats 'UBER'.
* A merchant is only suggested if EVERY one of its lines maps to the same category; mixed ones are reported as
  ambiguous and left alone.
* Results are stored as 'auto' suggestions (reviewable in the Auto-categorise tab). They never override manual
  choices, your rules, or transfers.
"""
import re
from collections import defaultdict

from . import autoclassify, classify, db

RULES: list[tuple[str, str, str]] = [
    # Groceries
    ("TESCO STORES", "Groceries", "high"), ("TESCO EXPRESS", "Groceries", "high"), ("ALDI", "Groceries", "high"),
    ("LIDL", "Groceries", "high"), ("SAINSBURY*", "Groceries", "high"), ("MORRISONS", "Groceries", "high"),
    ("WAITROSE", "Groceries", "high"), ("ASDA", "Groceries", "high"), ("OCADO", "Groceries", "high"),
    
    # Transport
    ("TFL", "Transport", "high"), ("TRAINLINE", "Transport", "high"), ("NATIONAL RAIL", "Transport", "high"),
    ("UBER", "Transport", "medium"), ("RINGGO", "Transport", "high"), ("GTR PARKING", "Transport", "high"),
    ("MFG", "Transport", "medium"),
    ("SHELL", "Transport", "medium"), ("ESSO", "Transport", "medium"),
    # Eating out
    ("GREGGS", "Eating out", "high"), ("KFC", "Eating out", "high"), ("DOMINO S", "Eating out", "high"),
    ("DOMINOS", "Eating out", "high"), ("DELIVEROO", "Eating out", "high"), ("UBER EATS", "Eating out", "high"),
    ("STARBUCKS", "Eating out", "high"), ("WASABI", "Eating out", "high"),
    ("MCDONALDS", "Eating out", "high"), ("ROADCHEF", "Eating out", "medium"),
    ("TRAVEL FOOD", "Eating out", "medium"),
    ("COSTA", "Eating out", "medium"), ("PRET", "Eating out", "medium"), ("NANDO S", "Eating out", "high"),
    ("PIZZA EXPRESS", "Eating out", "high"), ("WAGAMAMA", "Eating out", "high"), ("JUST EAT", "Eating out", "high"),
    ("SUBWAY", "Eating out", "medium"), ("BURGER KING", "Eating out", "high"), ("PIZZA HUT", "Eating out", "high"),
    ("SELECTA", "Eating out", "medium"),
    # Bills & utilities
    ("COUNCIL TAX", "Bills & utilities", "high"), ("TV LICENCE", "Bills & utilities", "high"),
    ("LYCAMOBILE", "Bills & utilities", "high"), ("LEBARA", "Bills & utilities", "high"),
    ("SKY TV", "Bills & utilities", "medium"), ("SKY DIGITAL", "Bills & utilities", "medium"),
    ("THAMES WATER", "Bills & utilities", "high"), ("BRITISH GAS", "Bills & utilities", "high"),
    ("OCTOPUS", "Bills & utilities", "medium"), ("EDF", "Bills & utilities", "high"),
    ("VIRGIN MEDIA", "Bills & utilities", "high"), ("VODAFONE", "Bills & utilities", "high"),
    ("BT GROUP", "Bills & utilities", "high"),
    # Housing
    # add your lender here, e.g. ("MY LENDER", "Mortgage payment", "high")
    # Shopping
    ("AMAZON", "Shopping", "medium"), ("AMZNMKTPLACE*", "Shopping", "medium"), ("EBAY", "Shopping", "medium"),
    ("TEMU", "Shopping", "high"), ("HALFORDS", "Shopping", "medium"),
    ("ARGOS", "Shopping", "high"), ("JOHN LEWIS", "Shopping", "medium"),
    ("IKEA", "Shopping", "high"),
    # Subscriptions
    ("AMAZON PRIME", "Subscriptions", "high"), ("APPLE COM", "Subscriptions", "medium"), ("NETFLIX", "Subscriptions", "high"),
    ("SPOTIFY", "Subscriptions", "high"), ("DISNEY PLUS", "Subscriptions", "high"), ("PRIME VIDEO", "Subscriptions", "high"),
    ("YOUTUBE", "Subscriptions", "medium"),
    # Health
    ("SPECSAVERS", "Health", "high"), ("NHSBSA", "Health", "high"), 
    ("BOOTS", "Health", "medium"), ("SUPERDRUG", "Health", "medium"),
    ("LLOYDS PHARMACY", "Health", "high"), ("PHARMACY*", "Health", "medium"),
    # Entertainment
    ("EURO DISNEY", "Entertainment", "high"), ("LONDON EYE", "Entertainment", "high"), ("CINEMA*", "Entertainment", "medium"),
    ("ODEON", "Entertainment", "high"), ("CINEWORLD", "Entertainment", "high"),
    # Travel
    ("AIRBNB", "Travel", "high"), ("LOVEHOLIDAY", "Travel", "high"), ("HM PASSPORT", "Travel", "medium"),
    ("BOOKING COM", "Travel", "high"), ("EASYJET", "Travel", "high"), ("RYANAIR", "Travel", "high"),
    ("BRITISH AIRWAYS", "Travel", "high"), ("EUROTUNNEL", "Travel", "medium"),
    # Kids & school
    ("ZEFFY", "Kids & school", "medium"),
    # Insurance
    ("AVIVA", "Insurance", "medium"), ("ADMIRAL", "Insurance", "medium"), ("DIRECT LINE", "Insurance", "high"),
    ("LEGAL GENERAL", "Insurance", "high"), ("INSURANCE*", "Insurance", "medium"),
    # Savings & investing
    ("TRADING 212", "Savings & investing", "high"),   # top-ups into the ISA
    ("FIDELITY", "Savings & investing", "high"),
    ("MONEYBOX", "Savings & investing", "high"), ("VANGUARD", "Savings & investing", "high"),
    # Income: add your employer here, e.g. ("ACME LTD", "Income", "medium")
]


def _compile():
    out = []
    for kw, cat, conf in RULES:
        prefix = kw.endswith("*")
        body = re.escape(kw.rstrip("*")).replace(r"\ ", " ")
        pat = r"(?<![A-Z0-9])" + body + ("" if prefix else r"(?![A-Z0-9])")
        out.append((re.compile(pat), len(kw.rstrip("*")), cat, conf, kw))
    return sorted(out, key=lambda r: -r[1])          # longest keyword first


_COMPILED = _compile()
_ORDER = {"low": 0, "medium": 1, "high": 2}


def _clean(desc: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", (desc or "").upper()).strip()


def match(description: str):
    """(category, confidence, keyword) for the best (longest) keyword, or None."""
    text = _clean(description)
    for rx, _, cat, conf, kw in _COMPILED:
        if rx.search(text):
            return cat, conf, kw
    return None


def plan() -> dict:
    """What applying the dictionary would do, by merchant. Only lines still at 'provider' are considered."""
    with db.connect() as con:
        rows = con.execute("SELECT description FROM transactions WHERE category_source='provider'").fetchall()
    by_key: dict[str, list[str]] = defaultdict(list)
    for (d,) in rows:
        k = classify.merchant_key(d or "")
        if k and k not in classify._GENERIC_KEYS:
            by_key[k].append(d or "")
    matched, ambiguous, unmatched = [], [], []
    for key, descs in by_key.items():
        hits = [match(d) for d in descs]
        cats = {h[0] for h in hits if h}
        if len(cats) == 1 and all(hits):
            conf = min((h[1] for h in hits), key=_ORDER.get)
            matched.append({"merchant": key, "category": cats.pop(), "confidence": conf, "lines": len(descs),
                            "keyword": hits[0][2]})
        elif cats:
            ambiguous.append({"merchant": key, "categories": sorted(cats), "lines": len(descs)})
        else:
            unmatched.append({"merchant": key, "lines": len(descs)})
    for lst in (matched, ambiguous, unmatched):
        lst.sort(key=lambda x: -x["lines"])
    return {"matched": matched, "ambiguous": ambiguous, "unmatched": unmatched}


def apply() -> dict:
    """Store the matched merchants as 'auto' suggestions and apply them. Existing suggestions are never overwritten."""
    p = plan()
    with db.connect() as con:
        con.executescript(autoclassify.SUGGESTIONS_SCHEMA)
        for m in p["matched"]:
            con.execute("INSERT OR IGNORE INTO merchant_suggestions (merchant_key, category, confidence, model)"
                        " VALUES (?,?,?,'local-dictionary')", (m["merchant"], m["category"], m["confidence"]))
    applied = autoclassify.apply_suggestions()
    return {"merchants": len(p["matched"]), "applied": applied, "ambiguous": len(p["ambiguous"]),
            "unmatched": len(p["unmatched"])}
