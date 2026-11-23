"""LLM-suggested categories for merchants.  PRIVACY MODEL (read before changing anything):

* Only a short, cleaned MERCHANT LABEL is ever sent (e.g. "TESCO STORES"). Never amounts, dates, accounts,
  full descriptions, or anything that looks like a person's name or an account reference.
* Labels that look personal (payments to individuals, account/reference numbers, your own name) are held back
  and shown to you as "not sent" - they get no suggestion and you categorise them by hand.
* Nothing is sent without an explicit preview() -> approve step in the UI/CLI.
* The client is built with an EXPLICIT personal key and the public API URL. It never reads ANTHROPIC_API_KEY,
  ANTHROPIC_AUTH_TOKEN or ANTHROPIC_BASE_URL, because on a work machine those point at a company gateway.
* Auto suggestions never override manual choices, your rules, or transfers (category_source order:
  manual > transfer > rule > auto > provider).
"""
import os
import re
from collections import defaultdict

from . import classify, db

MODEL = "claude-haiku-5-5"      # cheap and plenty for labelling merchants; override with FT_LLM_MODEL
API_URL = "https://api.anthropic.com"
BATCH = 80

DEFAULT_CATEGORIES = [
    "Groceries", "Eating out", "Transport", "Bills & utilities", "Mortgage payment", "Housing", "Shopping", "Health",
    "Entertainment", "Travel", "Kids & school", "Insurance", "Subscriptions", "Cash",
    "Savings & investing", "Income", "Other",
]

SUGGESTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS merchant_suggestions (
    merchant_key TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    confidence TEXT NOT NULL,          -- high / medium / low, as reported by the model
    model TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

# --- what must never leave the machine ---------------------------------------------------------------------
_PERSON_HINT = re.compile(r"\b(MR|MRS|MS|MISS|DR)\b\.?")
_REF_HINT = re.compile(r"\d{4,}|R\d{6,}|\bREF\b|\bMANDATE\b|\bA\s*/\s*C\b|\bACCOUNT\b|\bSAVING\b")
_MONEYISH = re.compile(r"[£$€]|\b\d+[.,]\d{2}\b")


def _own_names() -> set[str]:
    """Words from FT_OWN_NAMES (comma separated, in .env) that must never be sent, e.g. 'SMITH,JANE,JOHN'."""
    return {w.strip().upper() for w in os.getenv("FT_OWN_NAMES", "").split(",") if w.strip()}


def looks_personal(label: str, raw_samples: list[str]) -> str | None:
    """Return a reason if this merchant should be withheld, else None. Errs on the side of withholding."""
    up = label.upper()
    if not up:
        return "empty"
    if _PERSON_HINT.search(up) or _MONEYISH.search(up):
        return "contains a title or amount"
    own = _own_names()
    if own and any(n in up.split() or any(n in s.upper() for s in raw_samples) for n in own):
        return "contains a name from FT_OWN_NAMES"
    if any(_REF_HINT.search(s.upper()) for s in raw_samples[:3]) and any(
            k in " ".join(raw_samples[:3]).upper() for k in ("FASTER PAYMENT", "STANDING ORDER", "VIA MOBILE", "BANK TRANSFER", "MANDATE")):
        return "looks like a person-to-person or account payment"
    return None


def candidates(limit: int | None = None) -> dict:
    """Merchants needing a suggestion: {'send': [{'label', 'count', 'sample'}], 'withheld': [{'label','reason','count'}]}.

    Only transactions with no manual/rule/transfer decision and no stored suggestion are considered.
    """
    with db.connect() as con:
        con.executescript(SUGGESTIONS_SCHEMA)
        known = {r[0] for r in con.execute("SELECT merchant_key FROM merchant_suggestions")}
        rows = con.execute(
            "SELECT description FROM transactions WHERE category_source IN ('provider') AND amount_pence != 0").fetchall()
    groups: dict[str, list[str]] = defaultdict(list)
    for (desc,) in rows:
        key = classify.merchant_key(desc or "")
        if key and key not in classify._GENERIC_KEYS and key not in known:
            groups[key].append(desc or "")
    send, withheld = [], []
    for key, descs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        reason = looks_personal(key, descs)
        if reason:
            withheld.append({"label": key, "reason": reason, "count": len(descs)})
        else:
            send.append({"label": key, "count": len(descs), "sample": None})
    if limit:
        send = send[:limit]
    return {"send": send, "withheld": withheld}


def preview() -> str:
    """Exactly what would be sent. Print this and get approval before calling suggest()."""
    c = candidates()
    lines = [f"{len(c['send'])} merchant labels would be sent (labels only - no amounts, dates, accounts or descriptions):"]
    lines += [f"  {x['label']}" for x in c["send"]]
    lines += ["", f"{len(c['withheld'])} labels WITHHELD (look personal; categorise by hand):"]
    lines += [f"  {x['label']}   [{x['reason']}]" for x in c["withheld"]]
    return "\n".join(lines)


# --- the call ----------------------------------------------------------------------------------------------

def backend() -> str:
    """'anthropic' (personal key, public API; default) or 'gateway' (explicit opt-in, see _client)."""
    return os.getenv("FT_LLM_BACKEND", "anthropic").strip().lower()


def _client():
    import anthropic
    if backend() == "gateway":
        # Explicit opt-in to a company gateway. Everything comes from FT_* variables you set in .env on purpose;
        # the ambient ANTHROPIC_* variables are still never read. Labels you approve in preview() are logged by
        # whoever runs the gateway, and the credential is a work one - check your acceptable-use policy first.
        url = os.getenv("FT_GATEWAY_BASE_URL", "").strip()
        token = os.getenv("FT_GATEWAY_TOKEN", "").strip()
        if not (url and token and os.getenv("FT_LLM_MODEL", "").strip()):
            raise SystemExit("Gateway backend needs FT_GATEWAY_BASE_URL, FT_GATEWAY_TOKEN and FT_LLM_MODEL in .env "
                             "(the model name must be one the gateway exposes - ask its admins).")
        return anthropic.Anthropic(auth_token=token, base_url=url, max_retries=2, timeout=60.0)
    key = os.getenv("FT_ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "FT_ANTHROPIC_API_KEY is not set. Put a PERSONAL Anthropic API key in .env as FT_ANTHROPIC_API_KEY.\n"
            "This tool deliberately ignores ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL, which on "
            "this machine point at a company LLM gateway - personal finance data must not go through it.\n"
            "(To use the gateway on purpose, set FT_LLM_BACKEND=gateway plus FT_GATEWAY_BASE_URL, FT_GATEWAY_TOKEN, FT_LLM_MODEL.)")
    return anthropic.Anthropic(api_key=key, base_url=API_URL, max_retries=2, timeout=60.0)


def _ask(client, model: str, cats: list[str], chunk: list[str], Result):
    """Return a list of items, or None on refusal/unusable output. Tries structured output first; if a gateway
    rejects it, falls back to plain JSON in the reply (validated the same way)."""
    import json

    import anthropic
    user = "Allowed categories: " + ", ".join(cats) + "\n\nLabels:\n" + "\n".join(chunk)
    try:
        resp = client.messages.parse(model=model, max_tokens=8000, system=PROMPT,
                                     messages=[{"role": "user", "content": user}], output_format=Result)
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            return None
        return resp.parsed_output.items
    except anthropic.BadRequestError:
        if backend() != "gateway":
            raise
    resp = client.messages.create(
        model=model, max_tokens=8000,
        system=PROMPT + ' Reply with ONLY JSON: {"items":[{"label":..., "category":..., "confidence":"high|medium|low"}]}',
        messages=[{"role": "user", "content": user}])
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        raw = json.loads(text[text.index("{"):text.rindex("}") + 1])["items"]
    except (ValueError, KeyError, TypeError):
        return None
    # Validate item by item so one bad row (unknown category, wrong type) doesn't discard the valid ones.
    Item = Result.model_fields["items"].annotation.__args__[0]
    good = []
    for r in raw if isinstance(raw, list) else []:
        try:
            good.append(Item.model_validate(r))
        except ValueError:
            continue
    return good or None


def check() -> str:
    """Harmless connectivity test: one fake label, no personal data. Run this before suggest()."""
    cats = DEFAULT_CATEGORIES
    model = os.getenv("FT_LLM_MODEL", MODEL)
    items = _ask(_client(), model, cats, ["TESCO STORES"], _schema(cats))
    return (f"backend={backend()} model={model}: " +
            (f"OK - 'TESCO STORES' -> {items[0].category}" if items else "reply could not be used (refusal or bad format)"))


def _schema(categories: list[str]):
    from typing import Literal

    from pydantic import BaseModel

    Cat = Literal[tuple(categories)]  # type: ignore[valid-type]

    class Item(BaseModel):
        label: str
        category: Cat  # type: ignore[valid-type]
        confidence: Literal["high", "medium", "low"]

    class Result(BaseModel):
        items: list[Item]

    return Result


PROMPT = (
    "You categorise UK bank-statement merchant labels for a personal budget. Labels are truncated, upper-cased "
    "and may be unfamiliar local businesses. Choose exactly one category per label from the allowed list. "
    "Use 'Other' when genuinely unsure and mark confidence 'low'; do not guess a specific category for an unknown "
    "name. Return one item per input label, copying each label exactly."
)


def suggest(categories: list[str] | None = None, limit: int | None = None, client=None) -> dict:
    """Send APPROVED labels, store suggestions, and apply them to transactions that have no stronger decision."""
    cats = categories or DEFAULT_CATEGORIES
    c = candidates(limit)
    labels = [x["label"] for x in c["send"]]
    if not labels:
        return {"sent": 0, "stored": 0, "applied": 0, "withheld": len(c["withheld"])}
    client = client or _client()
    Result = _schema(cats)
    model = os.getenv("FT_LLM_MODEL", MODEL)
    stored = 0
    with db.connect() as con:
        con.executescript(SUGGESTIONS_SCHEMA)
        for i in range(0, len(labels), BATCH):
            chunk = labels[i:i + BATCH]
            items = _ask(client, model, cats, chunk, Result)
            if items is None:
                continue                                  # leave unsuggested; never guess on a refusal
            sent = set(chunk)
            for it in items:
                if it.label in sent:                      # ignore anything the model invented
                    con.execute("INSERT OR REPLACE INTO merchant_suggestions (merchant_key, category, confidence, model)"
                                " VALUES (?,?,?,?)", (it.label, it.category, it.confidence, model))
                    stored += 1
    return {"sent": len(labels), "stored": stored, "applied": apply_suggestions(), "withheld": len(c["withheld"])}


def apply_suggestions() -> int:
    """Write stored suggestions onto transactions still at 'provider'. Never touches manual/rule/transfer lines."""
    n = 0
    with db.connect() as con:
        con.executescript(SUGGESTIONS_SCHEMA)
        classify.ensure_categories(con)
        sug = {r["merchant_key"]: r["category"] for r in con.execute("SELECT merchant_key, category FROM merchant_suggestions")}
        for row in con.execute("SELECT id, description FROM transactions WHERE category_source='provider'").fetchall():
            cat = sug.get(classify.merchant_key(row["description"] or ""))
            if cat and cat != "Other":
                con.execute("UPDATE transactions SET category_id=?, category_source='auto' WHERE id=? AND category_source='provider'",
                            (classify._cat_id(con, cat), row["id"]))
                n += 1
    return n


def accept_all(min_confidence: str = "medium") -> int:
    """Promote auto suggestions to confirmed (manual) status, at or above a confidence level."""
    order = {"low": 0, "medium": 1, "high": 2}
    with db.connect() as con:
        con.executescript(SUGGESTIONS_SCHEMA)
        keep = {r["merchant_key"] for r in con.execute("SELECT merchant_key, confidence FROM merchant_suggestions")
                if order[con.execute("SELECT confidence FROM merchant_suggestions WHERE merchant_key=?",
                                     (r["merchant_key"],)).fetchone()[0]] >= order[min_confidence]}
        n = 0
        for row in con.execute("SELECT id, description FROM transactions WHERE category_source='auto'").fetchall():
            if classify.merchant_key(row["description"] or "") in keep:
                n += con.execute("UPDATE transactions SET category_source='manual' WHERE id=?", (row["id"],)).rowcount
    return n
