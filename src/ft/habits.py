"""Habit tools: fixed vs flexible spend, and a merchant watch-list with optional monthly limits.

Fixed = categories you cannot easily change month to month (mortgage, bills...). Flexible = everything else. The split is
a saved setting (categories, not transactions), so a new category defaults to flexible. Both views use the budget month and
the same 'real activity' rule as the rest of the app (transfers and 'not spend' categories excluded).
"""
import json

import pandas as pd

from . import data, db, periods

DEFAULT_FIXED = ("Mortgage payment", "Housing", "Bills & utilities", "Loan", "Subscriptions")
WATCH_SCHEMA = ("CREATE TABLE IF NOT EXISTS watchlist (id INTEGER PRIMARY KEY, label TEXT NOT NULL, pattern TEXT NOT NULL,"
                " monthly_limit_pence INTEGER)")


def get_fixed() -> tuple[str, ...]:
    with db.connect() as con:
        con.execute(periods.SETTINGS_SCHEMA)
        r = con.execute("SELECT value FROM settings WHERE key='fixed_categories'").fetchone()
    try:
        return tuple(str(v) for v in json.loads(r["value"])) if r else DEFAULT_FIXED
    except (TypeError, ValueError):
        return DEFAULT_FIXED


def set_fixed(names: list[str]):
    clean = sorted({n.strip() for n in names if n and n.strip()})
    with db.connect() as con:
        con.execute(periods.SETTINGS_SCHEMA)
        con.execute("INSERT INTO settings (key, value) VALUES ('fixed_categories', ?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(clean),))


def fixed_flexible(n: int = 12) -> pd.DataFrame:
    """Per budget month: income, fixed, flexible (pence), flexible share of spend, left over. Latest n months with data."""
    fixed = get_fixed()
    q = ",".join("?" * len(fixed)) if fixed else "''"
    sql = (f"SELECT {data._period()} AS month,"
           " SUM(CASE WHEN t.amount_pence>0 THEN t.amount_pence ELSE 0 END) AS income_pence,"
           f" -SUM(CASE WHEN t.amount_pence<0 AND COALESCE(c.name,'') IN ({q}) THEN t.amount_pence ELSE 0 END) AS fixed_pence,"
           f" -SUM(CASE WHEN t.amount_pence<0 AND COALESCE(c.name,'') NOT IN ({q}) THEN t.amount_pence ELSE 0 END) AS flexible_pence"
           f" FROM transactions t LEFT JOIN categories c ON c.id=t.category_id WHERE t.pending=0 AND {data._real_activity()}"
           " GROUP BY month ORDER BY month DESC LIMIT ?")
    df = data._df(sql, list(fixed) + list(fixed) + [n]).sort_values("month").reset_index(drop=True)
    spend = df["fixed_pence"] + df["flexible_pence"]
    df["flexible_share"] = (df["flexible_pence"] / spend.where(spend > 0)) * 100
    df["left_pence"] = df["income_pence"] - spend
    return df


# ---------- watch-list ----------
def _ensure():
    with db.connect() as con:
        con.execute(WATCH_SCHEMA)


def watchlist() -> pd.DataFrame:
    _ensure()
    return data._df("SELECT id, label, pattern, monthly_limit_pence FROM watchlist ORDER BY label")


def add_watch(label: str, pattern: str, limit_pence: int | None = None):
    label, pattern = (label or "").strip(), (pattern or "").strip()
    if not label or len(pattern) < 3:
        raise ValueError("Give a name and a pattern of at least 3 characters (so it doesn't match half your transactions).")
    if limit_pence is not None and limit_pence <= 0:
        limit_pence = None
    _ensure()
    with db.connect() as con:
        con.execute("INSERT INTO watchlist (label, pattern, monthly_limit_pence) VALUES (?,?,?)", (label, pattern, limit_pence))


def remove_watch(watch_id: int):
    _ensure()
    with db.connect() as con:
        con.execute("DELETE FROM watchlist WHERE id=?", (int(watch_id),))


def watch_by_month(n: int = 6) -> pd.DataFrame:
    """One row per (watch item, budget month): spend_pence, lines. Months with no spend are filled with 0 for the latest n
    months that have ANY data, so a quiet month shows as a real zero. Matches description case-insensitively (contains)."""
    w = watchlist()
    if w.empty:
        return pd.DataFrame(columns=["label", "month", "spend_pence", "lines", "monthly_limit_pence"])
    months = sorted(data.months())[-n:]
    rows = []
    for _, r in w.iterrows():
        pat = "%" + r["pattern"].lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        got = data._df(
            f"SELECT {data._period()} AS month, -SUM(t.amount_pence) AS spend_pence, COUNT(*) AS lines"
            " FROM transactions t LEFT JOIN categories c ON c.id=t.category_id"
            f" WHERE t.pending=0 AND t.amount_pence<0 AND {data._real_activity()} AND LOWER(t.description) LIKE ? ESCAPE '\\'"
            " GROUP BY month", (pat,)).set_index("month")
        for m in months:
            rows.append({"label": r["label"], "month": m,
                         "spend_pence": int(got.at[m, "spend_pence"]) if m in got.index else 0,
                         "lines": int(got.at[m, "lines"]) if m in got.index else 0,
                         "monthly_limit_pence": r["monthly_limit_pence"]})
    return pd.DataFrame(rows)
