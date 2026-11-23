"""Short-term protection (months of cover), long-term growth (savings rate, net worth over time) and remortgage inputs.

Definitions are deliberately simple and stated in the UI:
- Liquid cash = current + savings + card balances at your ownership share (a card balance is negative, so it reduces cash).
  ISAs, pensions, property and mortgage are NOT liquid.
- Months of cover = liquid cash / average monthly FIXED spend, over complete budget months only.
- Savings rate = money moved into the 'count as saved' categories / income, per budget month.
"""
import json
from datetime import date

import pandas as pd

from . import data, db, habits, periods

LIQUID_KINDS = ("current", "savings", "card")
LTV_BANDS = (50, 60, 70, 75, 80, 85, 90, 95)      # typical UK lender tiers; the exact tiers vary by lender


def liquid_cash_pence() -> int | None:
    acc = data.accounts_latest().dropna(subset=["balance_pence"])
    acc = acc[acc["kind"].isin(LIQUID_KINDS)]
    return None if acc.empty else int((acc["balance_pence"] * acc["share_pct"] / 100).round().sum())


def complete_months(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only budget months fully covered by synced data for EVERY account that has transactions: the month must start
    on/after the latest account's first transaction, and end on/before the last sync. This drops stub months where only one
    account had history, which would otherwise make average spend look too low."""
    span = data.data_span()
    if df.empty or not span:
        return df.iloc[0:0]
    firsts = data._df("SELECT MIN(substr(posted_at,1,10)) AS f FROM transactions GROUP BY account_id")["f"]
    first = max(date.fromisoformat(f) for f in firsts)
    sd = periods.get_start_day()
    return df[[not periods.is_partial(m, sd, first, span[1]) for m in df["month"]]].reset_index(drop=True)


def cover(n: int = 12) -> dict:
    ff = complete_months(habits.fixed_flexible(n))
    cash = liquid_cash_pence()
    if ff.empty or cash is None:
        return {"ok": False, "months_used": len(ff), "cash_pence": cash}
    avg_fixed = float(ff["fixed_pence"].mean())
    avg_total = float((ff["fixed_pence"] + ff["flexible_pence"]).mean())
    return {"ok": True, "months_used": len(ff), "cash_pence": cash, "avg_fixed_pence": avg_fixed, "avg_total_pence": avg_total,
            "cover_fixed": cash / avg_fixed if avg_fixed > 0 else None,
            "cover_total": cash / avg_total if avg_total > 0 else None}


def savings_rate(n: int = 12) -> pd.DataFrame:
    """Per budget month: income, saved (net outflow into the not-spend categories), rate %. Complete months only."""
    ns = periods.get_not_spend()
    if not ns:
        return pd.DataFrame(columns=["month", "income_pence", "saved_pence", "rate_pct"])
    q = ",".join("?" * len(ns))
    df = data._df(
        f"SELECT {data._period()} AS month,"
        f" SUM(CASE WHEN t.amount_pence>0 AND COALESCE(c.name,'')!='Transfer' AND COALESCE(c.name,'') NOT IN ({q}) THEN t.amount_pence ELSE 0 END) AS income_pence,"
        f" -SUM(CASE WHEN COALESCE(c.name,'') IN ({q}) THEN t.amount_pence ELSE 0 END) AS saved_pence"
        " FROM transactions t LEFT JOIN categories c ON c.id=t.category_id WHERE t.pending=0"
        " GROUP BY month ORDER BY month DESC LIMIT ?", list(ns) + list(ns) + [n])
    df = complete_months(df.sort_values("month").reset_index(drop=True))
    df["rate_pct"] = (df["saved_pence"] / df["income_pence"].where(df["income_pence"] > 0)) * 100
    return df


def net_worth_history() -> pd.DataFrame:
    """Net worth per snapshot date: each account's latest balance on or before that date, at your share. The series starts
    on the first date when every account has a balance, so it never mixes a partial set of accounts."""
    b = data._df("SELECT b.account_id, b.as_of, b.balance_pence, COALESCE(a.share_pct,100) AS share_pct"
                 " FROM balances b JOIN accounts a ON a.id=b.account_id")
    if b.empty:
        return pd.DataFrame(columns=["as_of", "net_pence", "accounts"])
    b["counted"] = b["balance_pence"] * b["share_pct"] / 100
    wide = b.pivot_table(index="as_of", columns="account_id", values="counted", aggfunc="last").sort_index().ffill()
    wide = wide[wide.notna().all(axis=1)]        # start once EVERY account has a balance, so early partial snapshots can't distort the line
    if wide.empty:
        return pd.DataFrame(columns=["as_of", "net_pence", "accounts"])
    out = pd.DataFrame({"as_of": wide.index, "net_pence": wide.sum(axis=1).round().astype(int).values,
                        "accounts": wide.notna().sum(axis=1).values})
    return out.reset_index(drop=True)


# ---------- remortgage inputs ----------
def get_mortgage_terms() -> dict:
    with db.connect() as con:
        con.execute(periods.SETTINGS_SCHEMA)
        r = con.execute("SELECT value FROM settings WHERE key='mortgage_terms'").fetchone()
    try:
        return json.loads(r["value"]) if r else {}
    except (TypeError, ValueError):
        return {}


def set_mortgage_terms(rate_pct: float | None, fix_end: date | None):
    if rate_pct is not None and not 0 <= rate_pct <= 25:
        raise ValueError("Rate must be between 0 and 25 (%).")
    with db.connect() as con:
        con.execute(periods.SETTINGS_SCHEMA)
        con.execute("INSERT INTO settings (key, value) VALUES ('mortgage_terms', ?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (json.dumps({"rate_pct": rate_pct, "fix_end": fix_end.isoformat() if fix_end else None}),))


def ltv_headroom(value_pence: int, mortgage_pence: int) -> dict:
    """Current LTV, the band it sits in, and the mortgage paydown needed to reach the next lower band."""
    if value_pence <= 0 or mortgage_pence < 0:
        return {"ok": False}
    ltv = mortgage_pence / value_pence * 100
    band = next((b for b in LTV_BANDS if ltv <= b), None)
    lower = [b for b in LTV_BANDS if b < ltv and (band is None or b < band)]
    nxt = max(lower) if lower else None
    return {"ok": True, "ltv": ltv, "band": band, "next_band": nxt,
            "paydown_pence": int(round(mortgage_pence - value_pence * nxt / 100)) if nxt is not None else 0}


def rate_cost(mortgage_pence: int, rate_pct: float, rise_pp: float = 1.0) -> dict:
    """Interest-only approximation of yearly interest and the extra yearly cost of a rate rise. Real repayments depend on
    term and amortisation, so treat these as a rough size, not a quote."""
    return {"annual_interest_pence": int(round(mortgage_pence * rate_pct / 100)),
            "extra_per_year_pence": int(round(mortgage_pence * rise_pp / 100))}


# ---------- ISA diversification (by what each fund holds, inferred from its NAME) ----------
def _asset_class(name: str) -> str:
    n = name.lower()
    if "gold" in n:
        return "Gold"
    if any(k in n for k in ("bond", "gilt", "treasury", "tips", "govt", "index-linked", "government")):
        return "Bonds"
    if "infrastructure" in n:
        return "Infrastructure"
    return "Equities"


def _region(name: str) -> str:
    n = name.lower()
    if "gold" in n:
        return "Global"
    for key, reg in (("all-world", "Global"), ("world", "Global"), ("global", "Global"),
                     ("em ", "Emerging"), ("emerging", "Emerging"), ("china", "Emerging"),
                     ("s&p 500", "US"), ("nasdaq", "US"), ("msci usa", "US"), ("usd", "US"), ("treasury", "US"), ("tips", "US"), ("japan", "Japan"),
                     ("europe", "Europe"), ("euro ", "Europe"), ("eur ", "Europe"),
                     ("ftse", "UK"), ("gilt", "UK"), ("gbp", "UK"), ("uk", "UK")):
        if key in n + " ":
            return reg
    return "Unclassified"


def diversification(vh: pd.DataFrame) -> dict:
    """vh = data.value_holdings(...) for ONE account. Classes and regions are guessed from fund names, so they are a
    guide, not a look-through of the underlying holdings (a 'Global' fund is mostly US in practice)."""
    if vh.empty or vh["value_gbp"].isna().all():
        return {"ok": False}
    d = vh.dropna(subset=["value_gbp"]).copy()
    total = d["value_gbp"].sum()
    d["asset_class"] = d["name"].map(_asset_class)
    d["region"] = d["name"].map(_region)
    d["pct"] = d["value_gbp"] / total * 100
    top = d.sort_values("value_gbp", ascending=False)
    # region view for equities only: the bond region mixes currency risk with credit risk and would blur the point
    eq = d[d["asset_class"] == "Equities"]
    us_eq = eq[eq["region"] == "US"]
    return {"ok": True, "total": total, "detail": d, "n": len(d),
            "by_class": d.groupby("asset_class")["pct"].sum().sort_values(ascending=False),
            "by_region_eq": (eq.groupby("region")["value_gbp"].sum() / eq["value_gbp"].sum() * 100).sort_values(ascending=False) if len(eq) else pd.Series(dtype=float),
            "top1": float(top["pct"].iloc[0]), "top1_name": top["name"].iloc[0],
            "top3": float(top["pct"].head(3).sum()),
            "us_eq_funds": int(len(us_eq)), "us_eq_pct_of_isa": float(us_eq["pct"].sum()),
            "unclassified_pct": float(d.loc[d["region"] == "Unclassified", "pct"].sum())}
