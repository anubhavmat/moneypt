"""Property: equity from your manual house value and mortgage, plus UK HPI price movement as CONTEXT ONLY.

The index never changes the stored value. It is a regional average by property type, not a valuation of your house.
Source: HM Land Registry UK House Price Index (free, no key). Latest month is published with a lag.
"""
import json
import urllib.error
import urllib.request
from datetime import date

from . import db

HPI_BASE = "https://landregistry.data.gov.uk/data/ukhpi/region"
# type -> HPI field suffix. 'average' uses the all-property index.
TYPE_FIELDS = {"detached": "Detached", "semi-detached": "SemiDetached", "terraced": "Terraced",
               "flat": "FlatMaisonette", "average": ""}

PROPERTY_SCHEMA = """
CREATE TABLE IF NOT EXISTS property_settings (
    account_id INTEGER PRIMARY KEY REFERENCES accounts(id),   -- the manual house account
    mortgage_account_id INTEGER REFERENCES accounts(id),      -- manual mortgage account
    hpi_region TEXT NOT NULL,                                  -- e.g. 'leeds'
    property_type TEXT NOT NULL                                -- detached / semi-detached / terraced / flat / average
);
"""


def init(con):
    con.executescript(PROPERTY_SCHEMA)


def save_settings(account_id: int, mortgage_account_id: int | None, region: str, ptype: str):
    region = (region or "").strip().lower().replace(" ", "-")
    if not region:
        raise ValueError("Region is required (e.g. leeds).")
    if ptype not in TYPE_FIELDS:
        raise ValueError(f"Property type must be one of: {', '.join(TYPE_FIELDS)}")
    with db.connect() as con:
        init(con)
        con.execute("INSERT INTO property_settings (account_id, mortgage_account_id, hpi_region, property_type)"
                    " VALUES (?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET mortgage_account_id=excluded.mortgage_account_id,"
                    " hpi_region=excluded.hpi_region, property_type=excluded.property_type",
                    (account_id, mortgage_account_id, region, ptype))


def get_settings():
    with db.connect() as con:
        init(con)
        return con.execute("SELECT * FROM property_settings LIMIT 1").fetchone()


def equity() -> dict | None:
    """House value (your manual figure, at your ownership share) minus the linked mortgage. None if not configured."""
    s = get_settings()
    if not s:
        return None
    with db.connect() as con:
        def latest(aid):
            return con.execute("SELECT balance_pence, as_of FROM balances WHERE account_id=? ORDER BY as_of DESC LIMIT 1",
                               (aid,)).fetchone()
        acc = con.execute("SELECT name, COALESCE(share_pct,100) AS share FROM accounts WHERE id=?", (s["account_id"],)).fetchone()
        h = latest(s["account_id"])
        m = latest(s["mortgage_account_id"]) if s["mortgage_account_id"] else None
        macc = con.execute("SELECT name, COALESCE(share_pct,100) AS share FROM accounts WHERE id=?",
                           (s["mortgage_account_id"],)).fetchone() if s["mortgage_account_id"] else None
    if not h:
        return None
    value = round(h["balance_pence"] * acc["share"] / 100)
    debt = round(abs(m["balance_pence"]) * macc["share"] / 100) if m else 0
    eq = value - debt
    return {"house": acc["name"], "value_pence": value, "value_as_of": h["as_of"], "mortgage_pence": debt,
            "mortgage_as_of": m["as_of"] if m else None, "equity_pence": eq,
            "ltv_pct": round(debt / value * 100, 1) if value > 0 else None,
            "region": s["hpi_region"], "type": s["property_type"],
            "stale_gap_days": abs((date.fromisoformat(h["as_of"]) - date.fromisoformat(m["as_of"])).days) if m else 0}


def _fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "finance-tracker/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def price_context(region: str, ptype: str, since: str, fetch=_fetch) -> dict:
    """Average price movement for region/type from month `since` (YYYY-MM) to the latest published month.

    Returns {'ok': False, 'reason': ...} on any failure; never raises, never guesses.
    """
    suffix = TYPE_FIELDS.get(ptype)
    if suffix is None:
        return {"ok": False, "reason": f"Unknown property type '{ptype}'."}
    price_key = "averagePrice" + suffix
    try:
        latest = fetch(f"{HPI_BASE}/{region}.json?_pageSize=1&_sort=-refMonth")
        items = latest["result"]["items"]
        if not items:
            return {"ok": False, "reason": f"No index data for region '{region}'. Check the spelling."}
        end_month = items[0].rsplit("/", 1)[-1]

        def month_price(month):
            d = fetch(f"{HPI_BASE}/{region}/month/{month}.json")["result"]["primaryTopic"]
            return d.get(price_key) if isinstance(d, dict) else None

        end_price, start_price = month_price(end_month), month_price(since)
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, IndexError) as e:
        return {"ok": False, "reason": f"Could not reach the price index ({type(e).__name__})."}
    if not end_price or not start_price:
        return {"ok": False, "reason": f"No {ptype} price published for {since} or {end_month}."}
    return {"ok": True, "region": region, "type": ptype, "from_month": since, "to_month": end_month,
            "from_price": start_price, "to_price": end_price,
            "change_pct": round((end_price / start_price - 1) * 100, 1)}
