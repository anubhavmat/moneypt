"""Read queries for the dashboard, returned as pandas DataFrames. Money columns are integer pence."""
import pandas as pd

from . import db


def _df(sql: str, params=()) -> pd.DataFrame:
    with db.connect() as con:
        return pd.read_sql_query(sql, con, params=params)


def accounts_latest() -> pd.DataFrame:
    return _df(
        "SELECT a.id, a.name, COALESCE(a.provider, '') AS provider, a.kind, a.source, COALESCE(a.share_pct, 100) AS share_pct,"
        " (SELECT balance_pence FROM balances b WHERE b.account_id=a.id ORDER BY as_of DESC LIMIT 1) AS balance_pence,"
        " (SELECT MAX(as_of) FROM balances b WHERE b.account_id=a.id) AS as_of"
        " FROM accounts a ORDER BY a.kind, a.name")


def manual_accounts() -> pd.DataFrame:
    return accounts_latest().query("source == 'manual'").reset_index(drop=True)


def balance_history(account_id: int) -> pd.DataFrame:
    return _df("SELECT as_of, balance_pence FROM balances WHERE account_id=? ORDER BY as_of DESC", (account_id,))


def connections() -> pd.DataFrame:
    return _df("SELECT id, env, consent_expires_at FROM connections WHERE provider='truelayer'")


def _period() -> str:
    """SQL expression for the budget-period key of t.posted_at, using the saved start day."""
    from . import periods
    return periods.sql_period("t.posted_at", periods.get_start_day())


def months() -> list[str]:
    """Period keys that have data, newest first. (Name kept for the callers; these are budget periods.)"""
    return _df(f"SELECT DISTINCT {_period().replace('t.posted_at', 'posted_at')} AS m FROM transactions ORDER BY m DESC")["m"].tolist()


def data_span():
    """(first, last) date with any transaction, or None."""
    from datetime import date
    r = _df("SELECT MIN(substr(posted_at,1,10)) AS a, MAX(substr(posted_at,1,10)) AS b FROM transactions")
    return (date.fromisoformat(r.at[0, "a"]), date.fromisoformat(r.at[0, "b"])) if r.at[0, "a"] else None


def _real_activity() -> str:
    """SQL fragment 'this line is real spending/income': not a transfer and not in the saved 'not spend' categories
    (e.g. Savings & investing). Read from settings on every call, so a change applies immediately."""
    from . import periods
    names = ["Transfer"] + [n for n in periods.get_not_spend() if n != "Transfer"]
    quoted = ",".join("'" + n.replace("'", "''") + "'" for n in names)
    return f"COALESCE(c.name,'') NOT IN ({quoted})"


def monthly_cashflow(n: int = 12) -> pd.DataFrame:
    return _df(
        f"SELECT {_period()} AS month,"
        " -SUM(CASE WHEN t.amount_pence<0 THEN t.amount_pence ELSE 0 END) AS spend_pence,"
        " SUM(CASE WHEN t.amount_pence>0 THEN t.amount_pence ELSE 0 END) AS income_pence, COUNT(*) AS lines"
        f" FROM transactions t LEFT JOIN categories c ON c.id=t.category_id WHERE t.pending=0 AND {_real_activity()}"
        " GROUP BY month ORDER BY month DESC LIMIT ?", (n,))


def category_trend(n: int = 12, exclude: tuple[str, ...] = ()) -> pd.DataFrame:
    """Spend per category per budget period (long format: month, category, spend_pence). Excludes transfers and
    any categories named in `exclude`. Covers the latest n periods that have data."""
    ex = ""
    params: list = []
    if exclude:
        ex = f" AND COALESCE(c.name,'Uncategorised') NOT IN ({','.join('?' * len(exclude))})"
        params += list(exclude)
    df = _df(
        f"SELECT {_period()} AS month, COALESCE(c.name,'Uncategorised') AS category, -SUM(t.amount_pence) AS spend_pence"
        f" FROM transactions t LEFT JOIN categories c ON c.id=t.category_id WHERE t.pending=0 AND t.amount_pence<0"
        f" AND {_real_activity()}{ex} GROUP BY month, category", params)
    keep = sorted(df["month"].unique())[-n:]
    return df[df["month"].isin(keep)].reset_index(drop=True)


GRAINS = ("Daily", "Weekly", "Monthly", "Quarterly", "Annual")


def spend_by_grain(grain: str, exclude: tuple[str, ...] = (), categories: tuple[str, ...] = ()) -> pd.DataFrame:
    """Spend per category per time bucket: columns bucket (sort key), label, category, spend_pence.

    Every grain is built from DAILY totals, so all grains reconcile exactly. 'Monthly' uses the budget month (starts on the
    chosen day); 'Quarterly' and 'Annual' group those budget months by the month/year in their key, so they stay consistent
    with the rest of the app. Weeks start on Monday. Transfers and 'not spend' categories are excluded as elsewhere.
    """
    from datetime import date, timedelta
    from . import periods
    if grain not in GRAINS:
        raise ValueError(f"grain must be one of {GRAINS}")
    where, params = "", []
    if exclude:
        where += f" AND COALESCE(c.name,'Uncategorised') NOT IN ({','.join('?' * len(exclude))})"
        params += list(exclude)
    if categories:
        where += f" AND COALESCE(c.name,'Uncategorised') IN ({','.join('?' * len(categories))})"
        params += list(categories)
    df = _df(
        "SELECT substr(t.posted_at,1,10) AS day, COALESCE(c.name,'Uncategorised') AS category, -SUM(t.amount_pence) AS spend_pence"
        f" FROM transactions t LEFT JOIN categories c ON c.id=t.category_id WHERE t.pending=0 AND t.amount_pence<0"
        f" AND {_real_activity()}{where} GROUP BY day, category", params)
    if df.empty:
        return pd.DataFrame(columns=["bucket", "label", "category", "spend_pence"])
    sd = periods.get_start_day()
    days = pd.to_datetime(df["day"])
    if grain == "Daily":
        df["bucket"] = df["day"]
        df["label"] = days.dt.strftime("%d %b")
    elif grain == "Weekly":
        monday = days - pd.to_timedelta(days.dt.weekday, unit="D")
        df["bucket"] = monday.dt.strftime("%Y-%m-%d")
        df["label"] = "w/c " + monday.dt.strftime("%d %b")
    else:
        pkey = df["day"].map(lambda d: periods.period_of(d, sd))              # budget-month key 'YYYY-MM'
        if grain == "Monthly":
            df["bucket"] = pkey
            df["label"] = pkey.map(lambda k: periods.label(k, sd))
        elif grain == "Quarterly":
            def q(k):
                y, m = int(k[:4]), int(k[5:])
                return f"{y}-Q{(m - 1) // 3 + 1}"
            df["bucket"] = pkey.map(q)
            df["label"] = df["bucket"].map(lambda b: b.replace("-", " "))
        else:
            df["bucket"] = pkey.str[:4]
            df["label"] = df["bucket"]
    out = df.groupby(["bucket", "label", "category"], as_index=False)["spend_pence"].sum()
    return out.sort_values(["bucket", "category"]).reset_index(drop=True)


def category_spend(month: str) -> pd.DataFrame:
    return _df(
        "SELECT COALESCE(c.name,'Uncategorised') AS category, -SUM(t.amount_pence) AS spend_pence, COUNT(*) AS lines"
        f" FROM transactions t LEFT JOIN categories c ON c.id=t.category_id WHERE t.pending=0 AND t.amount_pence<0"
        f" AND {_period()}=? AND {_real_activity()} GROUP BY category ORDER BY spend_pence DESC", (month,))


def top_merchants(month: str, n: int = 12) -> pd.DataFrame:
    """Group by the first 22 characters of the description (providers give no merchant name for most banks)."""
    raw = _df(
        "SELECT t.description, t.amount_pence FROM transactions t LEFT JOIN categories c ON c.id=t.category_id"
        f" WHERE t.pending=0 AND t.amount_pence<0 AND {_period()}=? AND {_real_activity()}", (month,))
    if raw.empty:
        return pd.DataFrame({"merchant": [], "spend_pence": [], "lines": []})
    raw["merchant"] = raw["description"].fillna("").str.strip().str.upper().str[:22]
    out = raw.groupby("merchant", as_index=False).agg(spend_pence=("amount_pence", lambda s: -s.sum()), lines=("amount_pence", "size"))
    return out.sort_values("spend_pence", ascending=False).head(n).reset_index(drop=True)


def auto_review() -> pd.DataFrame:
    """Auto-suggested categories grouped by merchant: one row per merchant so one decision covers every line."""
    from . import classify
    raw = _df("SELECT t.id, t.description, t.amount_pence, COALESCE(c.name,'Uncategorised') AS category"
              " FROM transactions t LEFT JOIN categories c ON c.id=t.category_id WHERE t.category_source='auto'")
    if raw.empty:
        return pd.DataFrame(columns=["merchant", "category", "lines", "spend_pence", "confidence", "ids"])
    raw["merchant"] = raw["description"].map(lambda d: classify.merchant_key(d or ""))
    conf = _df("SELECT merchant_key AS merchant, confidence FROM merchant_suggestions") \
        if "merchant_suggestions" in _df("SELECT name FROM sqlite_master WHERE type='table'")["name"].values \
        else pd.DataFrame(columns=["merchant", "confidence"])
    g = raw.groupby(["merchant", "category"], as_index=False).agg(
        lines=("id", "size"), spend_pence=("amount_pence", lambda s: -s[s < 0].sum()), ids=("id", list))
    g = g.merge(conf, on="merchant", how="left")
    g["confidence"] = g["confidence"].fillna("-")
    order = {"low": 0, "medium": 1, "high": 2, "-": 0}
    return g.sort_values(by=["confidence", "spend_pence"], key=lambda c: c.map(order) if c.name == "confidence" else -c
                         ).reset_index(drop=True)


def category_names() -> list[str]:
    return _df("SELECT name FROM categories ORDER BY name")["name"].tolist()


def transactions(month: str | None = None, account: str | None = None, category: str | None = None,
                 text: str = "", include_transfers: bool = False, limit: int = 300) -> pd.DataFrame:
    where, params = ["1=1"], []
    if month:
        where.append(f"{_period()}=?"); params.append(month)
    if account:
        where.append("a.name=?"); params.append(account)
    if category:
        where.append("COALESCE(c.name,'Uncategorised')=?"); params.append(category)
    if text:
        where.append("LOWER(t.description) LIKE ?"); params.append(f"%{text.lower()}%")
    if not include_transfers:
        where.append("COALESCE(c.name,'') != 'Transfer'")   # the list keeps savings lines visible so you can recategorise them
    params.append(limit)
    return _df(
        "SELECT t.id, substr(t.posted_at,1,10) AS date, a.name AS account, t.description, t.amount_pence, t.pending,"
        " COALESCE(c.name,'Uncategorised') AS category, t.category_source AS source"
        " FROM transactions t JOIN accounts a ON a.id=t.account_id LEFT JOIN categories c ON c.id=t.category_id"
        f" WHERE {' AND '.join(where)} ORDER BY t.posted_at DESC, t.id DESC LIMIT ?", params)


def holdings() -> pd.DataFrame:
    with db.connect() as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='positions'").fetchone():
            return pd.DataFrame()
    return _df(
        "SELECT a.id AS account_id, a.name AS account, p.ticker, p.name, p.quantity, p.avg_price, p.current_price, p.currency,"
        " p.as_of, (SELECT balance_pence FROM balances b WHERE b.account_id=a.id ORDER BY as_of DESC LIMIT 1) AS account_total_pence"
        " FROM positions p JOIN accounts a ON a.id=p.account_id"
        " WHERE p.as_of=(SELECT MAX(as_of) FROM positions WHERE account_id=p.account_id) ORDER BY p.name")


def value_holdings(h: pd.DataFrame) -> pd.DataFrame:
    """Add value in POUNDS per position so rows are comparable and sum to the account total.

    GBP as is; GBX (pence) / 100; any other currency (EUR) needs a rate. Trading 212 does not give us a rate, so it is
    DERIVED per account: (account total - value of GBP/GBX holdings) / native value of the other-currency holdings.
    That makes everything add up to the account total, but the foreign rows are estimates (flag 'est'), and any gap
    that is NOT explained by foreign holdings is reported as 'Cash' (uninvested balance), never hidden.
    """
    if h.empty:
        return h
    out = []
    for _, g in h.groupby("account_id"):
        g = g.copy()
        total = g["account_total_pence"].iloc[0] / 100 if pd.notna(g["account_total_pence"].iloc[0]) else None
        native = g["quantity"] * g["current_price"]
        pounds = native.where(g["currency"] != "GBX", native / 100)
        base = g["currency"].isin(["GBP", "GBX"])
        g["value_gbp"] = pounds.where(base)
        g["est"] = ~base
        foreign_native = native[~base].sum()
        base_total = g.loc[base, "value_gbp"].sum()
        if total is not None and foreign_native > 0:
            rate = (total - base_total) / foreign_native
            if 0.3 < rate < 3:                               # a plausible FX rate; otherwise do not trust the derivation
                g.loc[~base, "value_gbp"] = native[~base] * rate
                g["fx_rate"] = rate
            else:
                g["fx_rate"] = float("nan")
        else:
            g["fx_rate"] = float("nan")
        out.append(g)
    res = pd.concat(out)
    totals = res.groupby("account_id")["value_gbp"].transform("sum")
    res["pct_of_positions"] = res["value_gbp"] / totals * 100
    return res
