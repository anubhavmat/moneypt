"""Budget periods: a 'month' that starts on a chosen day (e.g. the 21st, when pay arrives).

A period is named by the calendar month it STARTS in: with start_day=21, period '2026-10' runs 2026-10-21 to
2026-11-20 inclusive. start_day=1 gives ordinary calendar months. start_day is limited to 1-28 so every month has it.
"""
import re
from datetime import date

from . import db

DEFAULT_START_DAY = 21
SETTINGS_SCHEMA = "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"


def get_start_day() -> int:
    with db.connect() as con:
        con.execute(SETTINGS_SCHEMA)
        r = con.execute("SELECT value FROM settings WHERE key='period_start_day'").fetchone()
    try:
        return min(28, max(1, int(r["value"]))) if r else DEFAULT_START_DAY
    except (TypeError, ValueError):
        return DEFAULT_START_DAY


def set_start_day(day: int):
    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 28:
        raise ValueError("Start day must be a whole number from 1 to 28.")
    with db.connect() as con:
        con.execute(SETTINGS_SCHEMA)
        con.execute("INSERT INTO settings (key, value) VALUES ('period_start_day', ?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(day),))


DEFAULT_NOT_SPEND = ("Savings & investing",)     # 'Transfer' is always excluded in addition to these


def get_not_spend() -> tuple[str, ...]:
    """Categories that are saved or invested rather than spent. Excluded from spend, income and trend totals."""
    import json
    with db.connect() as con:
        con.execute(SETTINGS_SCHEMA)
        r = con.execute("SELECT value FROM settings WHERE key='not_spend_categories'").fetchone()
    try:
        vals = json.loads(r["value"]) if r else list(DEFAULT_NOT_SPEND)
        return tuple(str(v) for v in vals if str(v).strip())
    except (TypeError, ValueError):
        return DEFAULT_NOT_SPEND


def set_not_spend(names: list[str]):
    import json
    clean = sorted({n.strip() for n in names if n and n.strip() and n.strip() != "Transfer"})
    with db.connect() as con:
        con.execute(SETTINGS_SCHEMA)
        con.execute("INSERT INTO settings (key, value) VALUES ('not_spend_categories', ?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(clean),))


def period_of(d: date | str, start_day: int) -> str:
    """Period key 'YYYY-MM' that the date falls in."""
    d = date.fromisoformat(d[:10]) if isinstance(d, str) else d
    y, m = (d.year, d.month) if d.day >= start_day else ((d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1))
    return f"{y:04d}-{m:02d}"


def bounds(key: str, start_day: int) -> tuple[date, date]:
    """(first_day, last_day) inclusive for a period key."""
    if not re.fullmatch(r"\d{4}-\d{2}", key or ""):
        raise ValueError(f"Bad period key: {key!r}")
    y, m = int(key[:4]), int(key[5:])
    start = date(y, m, start_day)
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    from datetime import timedelta
    return start, date(ny, nm, start_day) - timedelta(days=1)


def label(key: str, start_day: int) -> str:
    s, e = bounds(key, start_day)
    if start_day == 1:
        return s.strftime("%b %Y")
    return f"{s.strftime('%b %Y')} ({s.day} {s.strftime('%b')}–{e.day} {e.strftime('%b')})"


def sql_period(col: str, start_day: int) -> str:
    """SQL expression turning a timestamp column into its period key. start_day is validated as an int, so it is
    safe to inline."""
    sd = int(start_day)
    if not 1 <= sd <= 28:
        raise ValueError("start_day out of range")
    return (f"strftime('%Y-%m', date(substr({col},1,10), CASE WHEN CAST(substr({col},9,2) AS INTEGER) >= {sd} "
            f"THEN 'start of month' ELSE '-1 month' END)) ")


def is_partial(key: str, start_day: int, first_data: date, last_data: date) -> bool:
    """True if the data doesn't cover the whole period (data begins after it starts, or it hasn't ended yet)."""
    s, e = bounds(key, start_day)
    return first_data > s or last_data < e
