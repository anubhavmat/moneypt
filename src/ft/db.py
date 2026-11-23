"""SQLite storage. Money is stored as integer pence (never floats)."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,              -- e.g. 'truelayer'
    env TEXT,                            -- 'sandbox' / 'live': never sync a connection in the wrong env
    label TEXT,                          -- bank name as returned by the provider
    access_token_enc TEXT,
    refresh_token_enc TEXT,
    access_expires_at TEXT,              -- ISO timestamp
    consent_expires_at TEXT,             -- UK reconfirmation is ~180 days from consent
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    connection_id INTEGER REFERENCES connections(id),
    external_id TEXT,                    -- provider account id; NULL for manual accounts
    source TEXT NOT NULL,                -- truelayer / trading212 / manual
    kind TEXT NOT NULL,                  -- current / savings / card / isa / pension / mortgage
    name TEXT NOT NULL,
    currency TEXT DEFAULT 'GBP',
    share_pct INTEGER DEFAULT 100,       -- your ownership share (joint accounts: 50)
    UNIQUE (source, external_id)
);

-- One row per account per day. Liabilities (cards, mortgage) are stored as negative pence.
CREATE TABLE IF NOT EXISTS balances (
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    as_of TEXT NOT NULL,                 -- YYYY-MM-DD
    balance_pence INTEGER NOT NULL,
    PRIMARY KEY (account_id, as_of)
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS merchant_rules (
    id INTEGER PRIMARY KEY,
    pattern TEXT NOT NULL,               -- case-insensitive substring match
    category_id INTEGER NOT NULL REFERENCES categories(id),
    priority INTEGER NOT NULL DEFAULT 100   -- lower number wins
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    external_id TEXT NOT NULL,           -- provider transaction id, used to dedupe re-syncs
    posted_at TEXT NOT NULL,
    description TEXT,
    merchant TEXT,
    amount_pence INTEGER NOT NULL,       -- negative = money out
    pending INTEGER NOT NULL DEFAULT 0,
    provider_category TEXT,
    category_id INTEGER REFERENCES categories(id),
    category_source TEXT NOT NULL DEFAULT 'provider',   -- manual > rule > provider
    UNIQUE (account_id, external_id)
);
"""


def db_path() -> str:
    """FT_DB_PATH if set (and not blank), else finance.db in the project folder, never in whatever folder you started from."""
    p = os.getenv("FT_DB_PATH", "").strip()
    return p or str(Path(__file__).resolve().parents[2] / "finance.db")


@contextmanager
def connect():
    con = sqlite3.connect(db_path())
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init():
    with connect() as con:
        con.executescript(SCHEMA)
        cols = [r["name"] for r in con.execute("PRAGMA table_info(connections)")]
        if "env" not in cols:   # DB created before the env column existed
            con.execute("ALTER TABLE connections ADD COLUMN env TEXT")
        acols = [r["name"] for r in con.execute("PRAGMA table_info(accounts)")]
        if "share_pct" not in acols:   # DB created before ownership share existed
            con.execute("ALTER TABLE accounts ADD COLUMN share_pct INTEGER DEFAULT 100")
        if "provider" not in acols:    # bank / institution name (NatWest, Monzo, ...)
            con.execute("ALTER TABLE accounts ADD COLUMN provider TEXT")
        # Trading 212 is always its own provider. Safe to set repeatedly and never overwrites a value.
        con.execute("UPDATE accounts SET provider='Trading 212' WHERE source='trading212' AND provider IS NULL")
        _migrate(con)


def _migrate(con):
    """One-time data fixes, each guarded by a marker so it can never run twice."""
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    done = {r["key"] for r in con.execute("SELECT key FROM meta")}
    if "card_sign_flipped" not in done:
        # Earlier syncs stored card amounts as TrueLayer reports them (purchases positive). Flip once.
        n = con.execute("UPDATE transactions SET amount_pence = -amount_pence WHERE account_id IN"
                        " (SELECT id FROM accounts WHERE kind='card')").rowcount
        con.execute("INSERT INTO meta VALUES ('card_sign_flipped', ?)", (str(n),))
        if n:
            print(f"Migration: flipped sign on {n} card transactions (purchases are now negative).")


def to_pence(amount) -> int:
    """Convert a provider decimal amount (e.g. -12.34) to integer pence without float error."""
    from decimal import Decimal, ROUND_HALF_UP
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
