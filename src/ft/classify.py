"""Transfer detection and rule-based categorisation.

Convention: amount_pence is negative when money leaves YOU, for every account type (cards are flipped at ingest).

Order of precedence for a transaction's category (category_source): manual > rule > transfer-pairing > provider.
Transfers (own-account moves, card repayments, ISA top-ups) are excluded from spend/income reports.

Matching is deliberately conservative: a pair is only auto-linked when amount and date line up AND the two sides are
in different accounts. Anything ambiguous is left alone and shown by `review`, never guessed.
"""
import re
from datetime import date, timedelta

from . import db

TRANSFER = "Transfer"

def builtin_transfer_patterns() -> list[str]:
    """Descriptions that are always an internal movement of your own money.

    Yours depend on your banks and your name, so list them in .env as FT_TRANSFER_PATTERNS: regular expressions separated
    by '|||', matched case-insensitively against the transaction description, e.g.
        FT_TRANSFER_PATTERNS=TRANSFER TO MR J SMITH|||REGULAR TRANSFER PAYMENT TO ACCOUNT SAVING
    Nothing is assumed by default. Extend further with `rules add`."""
    import os
    return [p.strip() for p in os.getenv("FT_TRANSFER_PATTERNS", "").split("|||") if p.strip()]
# Card repayments: which current-account line pays which card. Matched by amount+date, not text alone.
CARD_PAYMENT_HINTS = [r"DIRECT DEBIT PAYMENT \d+", r"PAYMENT BY DIRECT DEBIT", r"NATWEST", r"BARCLAYCARD", r"PAYMENT.*THANK"]

DATE_WINDOW_DAYS = 3


def ensure_categories(con):
    for name in (TRANSFER, "Uncategorised"):
        con.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (name,))


def _cat_id(con, name):
    ensure_categories(con)
    con.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (name,))
    return con.execute("SELECT id FROM categories WHERE name=?", (name,)).fetchone()["id"]


def _day(ts: str) -> date:
    return date.fromisoformat(ts[:10])


def mark_known_transfers(con) -> int:
    """Tag lines matching built-in own-account patterns. Skips anything the user set manually."""
    tid = _cat_id(con, TRANSFER)
    n = 0
    for pat in builtin_transfer_patterns():
        for row in con.execute("SELECT id, description FROM transactions WHERE category_source != 'manual'"):
            if re.search(pat, row["description"] or "", re.I):
                con.execute("UPDATE transactions SET category_id=?, category_source='transfer' WHERE id=?",
                            (tid, row["id"]))
                n += 1
    return n


def pair_transfers(con) -> dict:
    """Link opposite-signed, equal-amount transactions in DIFFERENT accounts within a few days.

    Only unambiguous pairs are linked (exactly one candidate each way). Ambiguous ones are counted and left for review.
    """
    tid = _cat_id(con, TRANSFER)
    rows = con.execute(
        "SELECT t.id, t.account_id, t.posted_at, t.amount_pence, t.description FROM transactions t"
        " WHERE t.category_source != 'manual' AND t.pending = 0 AND t.amount_pence != 0").fetchall()
    outs = [r for r in rows if r["amount_pence"] < 0]
    ins = [r for r in rows if r["amount_pence"] > 0]

    def candidates(o):
        return [i for i in ins if i["account_id"] != o["account_id"] and i["amount_pence"] == -o["amount_pence"]
                and abs((_day(i["posted_at"]) - _day(o["posted_at"])).days) <= DATE_WINDOW_DAYS]

    linked, ambiguous = 0, 0
    cand_for = {o["id"]: candidates(o) for o in outs}
    claimed_by_in: dict[int, list[int]] = {}
    for oid, cs in cand_for.items():
        for i in cs:
            claimed_by_in.setdefault(i["id"], []).append(oid)
    for o in outs:
        cs = cand_for[o["id"]]
        if len(cs) == 1 and len(claimed_by_in[cs[0]["id"]]) == 1:
            for tx_id in (o["id"], cs[0]["id"]):
                con.execute("UPDATE transactions SET category_id=?, category_source='transfer' WHERE id=?", (tid, tx_id))
            linked += 2
        elif len(cs) >= 1:
            ambiguous += 1
    return {"linked_lines": linked, "ambiguous_outgoing": ambiguous}


def apply_rules(con) -> int:
    """Apply user merchant rules (lowest priority number wins). Never overrides manual or transfer."""
    n = 0
    # Lower priority number wins; among equal priorities the NEWEST rule wins (your latest correction beats older ones).
    rules = con.execute("SELECT pattern, category_id FROM merchant_rules ORDER BY priority, id DESC").fetchall()
    # 'auto' (dictionary / AI suggestions) is weaker than your rules, so a rule may replace it.
    for row in con.execute("SELECT id, description FROM transactions WHERE category_source IN ('provider','auto','rule')"):
        for r in rules:
            if r["pattern"].lower() in (row["description"] or "").lower():
                con.execute("UPDATE transactions SET category_id=?, category_source='rule' WHERE id=?",
                            (r["category_id"], row["id"]))
                n += 1
                break
    return n


_NOISE = {"GBR", "ENG", "UK", "GB", "VIA", "MOBILE", "PY"}
# Keys that say nothing about WHO was paid; matching on them would group unrelated lines, so never suggest them.
_GENERIC_KEYS = {"", "A C", "DIRECT DEBIT", "STANDING ORDER", "PAYPAL PAYMENT", "FASTER PAYMENTS", "FP REJECT",
                 "BANK TRANSFER", "CARD PAYMENT", "CASH", "ATM", "INTEREST"}


def merchant_key(description: str) -> str:
    """Stable merchant label: uppercase, punctuation stripped, tokens containing digits (store numbers, card
    suffixes, references) and location noise dropped, then the first two words (three after PAYPAL)."""
    s = re.sub(r"[^A-Z0-9 ]+", " ", (description or "").upper())
    toks = [t for t in s.split() if not any(ch.isdigit() for ch in t) and t not in _NOISE]
    if not toks:
        return ""
    return " ".join(toks[:3 if toks[0] == "PAYPAL" else 2])


def find_similar(txn_id: int, category: str, limit: int = 200) -> list[dict]:
    """Other transactions that look like txn_id and are not already `category`.

    match='exact': same merchant key.  match='related': same first word only (offer, but leave unticked).
    Never offers manual or transfer lines, and only the same direction (a refund is not a purchase).
    """
    with db.connect() as con:
        src = con.execute("SELECT description, amount_pence FROM transactions WHERE id=?", (txn_id,)).fetchone()
        if not src:
            return []
        key = merchant_key(src["description"])
        first = key.split()[0] if key else ""
        if key in _GENERIC_KEYS or len(first) < 3:
            return []
        neg = src["amount_pence"] < 0
        rows = con.execute(
            "SELECT t.id, substr(t.posted_at,1,10) AS date, a.name AS account, t.description, t.amount_pence,"
            " COALESCE(c.name,'Uncategorised') AS current_category FROM transactions t"
            " JOIN accounts a ON a.id=t.account_id LEFT JOIN categories c ON c.id=t.category_id"
            " WHERE t.id != ? AND t.category_source IN ('provider','auto','rule')"
            " AND COALESCE(c.name,'') != ? AND (t.amount_pence < 0) = ? AND t.amount_pence != 0"
            " ORDER BY t.posted_at DESC", (txn_id, category, neg)).fetchall()
    out = []
    for r in rows:
        k = merchant_key(r["description"])
        if k in _GENERIC_KEYS:
            continue
        if k == key:
            out.append({**dict(r), "match": "exact"})
        elif k.split()[:1] == [first]:
            out.append({**dict(r), "match": "related"})
    out.sort(key=lambda d: d["match"] != "exact")      # stable: exact first, newest first within each group
    return out[:limit]


def apply_to_many(ids: list[int], category: str, rule_pattern: str | None = None) -> int:
    """Set `category` (as a manual choice) on the confirmed ids. Optionally also save a rule for future syncs.
    Transfers are never touched. Returns how many rows changed."""
    category = (category or "").strip()
    if not category:
        raise ValueError("Pick or enter a category.")
    if rule_pattern is not None and len(rule_pattern.strip()) < 3:
        raise ValueError("The text to match must be at least 3 characters.")
    with db.connect() as con:
        cid = _cat_id(con, category)
        n = 0
        for i in ids:
            n += con.execute("UPDATE transactions SET category_id=?, category_source='manual'"
                             " WHERE id=? AND category_source != 'transfer'", (cid, int(i))).rowcount
        if rule_pattern:
            con.execute("INSERT INTO merchant_rules (pattern, category_id, priority) VALUES (?,?,100)",
                        (rule_pattern.strip(), cid))
    return n


def merge_category(old: str, new: str) -> dict:
    """Fold category `old` into `new`: transactions, rules and stored suggestions move, then `old` is deleted.

    Keeps each line's category_source (a manual choice stays manual). Refuses to merge a category into itself, into
    a missing target, or to touch 'Transfer'/'Uncategorised'.
    """
    old, new = (old or "").strip(), (new or "").strip()
    if not old or not new or old == new:      # exact match: 'Eating Out' vs 'Eating out' are different rows
        raise ValueError("Pick two different categories.")
    if {old, new} & {"Transfer", "Uncategorised"}:
        raise ValueError("Transfer and Uncategorised cannot be merged.")
    with db.connect() as con:
        o = con.execute("SELECT id FROM categories WHERE name=?", (old,)).fetchone()
        n = con.execute("SELECT id FROM categories WHERE name=?", (new,)).fetchone()
        if not o:
            raise ValueError(f"No category called '{old}'.")
        if not n:
            raise ValueError(f"No category called '{new}'. Create it first.")
        tx = con.execute("UPDATE transactions SET category_id=? WHERE category_id=?", (n["id"], o["id"])).rowcount
        rules = con.execute("UPDATE merchant_rules SET category_id=? WHERE category_id=?", (n["id"], o["id"])).rowcount
        sug = 0
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='merchant_suggestions'").fetchone():
            sug = con.execute("UPDATE merchant_suggestions SET category=? WHERE category=?", (new, old)).rowcount
        con.execute("DELETE FROM categories WHERE id=?", (o["id"],))
    return {"transactions": tx, "rules": rules, "suggestions": sug}


def decide_merchant(pattern: str, category: str) -> dict:
    """Your standing decision: every transaction whose description contains `pattern` is `category`.

    Re-points lines that were only auto/provider/rule suggestions (also lines you set to a DIFFERENT category earlier
    are NOT touched, and neither are transfers), saves a rule for future syncs, and removes any stale stored
    suggestion for the matching merchants. Returns counts so the caller can report exactly what changed.
    """
    pattern, category = (pattern or "").strip(), (category or "").strip()
    if len(pattern) < 3:
        raise ValueError("The text to match must be at least 3 characters.")
    if not category:
        raise ValueError("Pick or enter a category.")
    like = f"%{pattern.lower()}%"
    with db.connect() as con:
        cid = _cat_id(con, category)
        con.execute("DELETE FROM merchant_rules WHERE LOWER(pattern)=LOWER(?)", (pattern,))
        con.execute("INSERT INTO merchant_rules (pattern, category_id, priority) VALUES (?,?,10)", (pattern, cid))
        moved = con.execute(
            "UPDATE transactions SET category_id=?, category_source='manual' WHERE LOWER(description) LIKE ?"
            " AND category_source IN ('auto','provider','rule') AND (category_id IS NULL OR category_id != ?"
            " OR category_source != 'manual')", (cid, like, cid)).rowcount
        keep_manual = con.execute(
            "SELECT COUNT(*) FROM transactions WHERE LOWER(description) LIKE ? AND category_source='manual' AND category_id != ?",
            (like, cid)).fetchone()[0]
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='merchant_suggestions'").fetchone():
            for (d,) in con.execute("SELECT DISTINCT description FROM transactions WHERE LOWER(description) LIKE ?", (like,)).fetchall():
                con.execute("DELETE FROM merchant_suggestions WHERE merchant_key=?", (merchant_key(d or ""),))
    return {"re-pointed": moved, "kept (you set a different category earlier)": keep_manual}


def count_matches(pattern: str) -> int:
    """How many not-yet-manual, non-transfer transactions a rule with this text would re-categorise."""
    if not pattern or len(pattern.strip()) < 3:
        return 0
    with db.connect() as con:
        return con.execute(
            "SELECT COUNT(*) FROM transactions WHERE category_source IN ('provider','auto','rule')"
            " AND LOWER(description) LIKE ?", (f"%{pattern.strip().lower()}%",)).fetchone()[0]


def recategorise(txn_id: int, category: str, every_time: bool = False, pattern: str | None = None) -> int:
    """Set one transaction's category (manual, beats everything). With every_time, also save a rule and apply it to
    other provider/rule-categorised lines. Returns how many OTHER transactions the rule changed."""
    category = (category or "").strip()
    if not category:
        raise ValueError("Pick or enter a category.")
    if every_time and len((pattern or "").strip()) < 3:
        raise ValueError("The text to match must be at least 3 characters.")
    with db.connect() as con:
        cid = _cat_id(con, category)
        if not con.execute("UPDATE transactions SET category_id=?, category_source='manual' WHERE id=?",
                           (cid, txn_id)).rowcount:
            raise ValueError("Transaction not found.")
        if not every_time:
            return 0
        con.execute("INSERT INTO merchant_rules (pattern, category_id, priority) VALUES (?,?,100)",
                    (pattern.strip(), cid))
        return apply_rules(con)


def run_all() -> dict:
    with db.connect() as con:
        ensure_categories(con)
        a = apply_rules(con)
        b = mark_known_transfers(con)
        c = pair_transfers(con)
        return {"rules_applied": a, "known_transfers": b, **c}


def add_rule(pattern: str, category: str, priority: int = 100):
    with db.connect() as con:
        cid = _cat_id(con, category)
        con.execute("INSERT INTO merchant_rules (pattern, category_id, priority) VALUES (?,?,?)",
                    (pattern, cid, priority))
    print(f"Rule added: '{pattern}' -> {category}. Run `classify` to apply.")


def review(limit: int = 25):
    """Show what's unresolved: card-payment-like lines that didn't pair, so you can see what needs a decision."""
    with db.connect() as con:
        print("== Payment-like lines NOT linked to a counterpart (possible missing account or ambiguity)")
        pat = "|".join(CARD_PAYMENT_HINTS)
        for r in con.execute(
                "SELECT a.name, substr(t.posted_at,1,10) d, t.amount_pence, substr(t.description,1,55) desc"
                " FROM transactions t JOIN accounts a ON a.id=t.account_id"
                " WHERE t.category_source != 'transfer' ORDER BY t.posted_at DESC"):
            if re.search(pat, r["desc"] or "", re.I):
                print(f"  {r['d']}  {r['name'][:22]:22} {r['amount_pence'] / 100:>10,.2f}  {r['desc']}")
                limit -= 1
                if limit <= 0:
                    break
