"""Manual accounts (savings the bank API doesn't expose, pensions, mortgage) with dated balances.

add_account / save_balance / set_share raise ValueError (used by the dashboard); add / set_balance are the CLI wrappers.
"""
from datetime import date

from . import db

KINDS = ("savings", "pension", "mortgage", "isa", "current", "card", "other")
LIABILITIES = ("mortgage", "card")


def set_provider(account_id: int, provider: str):
    """Bank / institution for any account (blank clears it)."""
    provider = (provider or "").strip() or None
    with db.connect() as con:
        if not con.execute("UPDATE accounts SET provider=? WHERE id=?", (provider, account_id)).rowcount:
            raise ValueError("Account not found.")


def add_account(kind: str, name: str, share_pct: int = 100, provider: str = ""):
    name = (name or "").strip()
    if kind not in KINDS:
        raise ValueError(f"Type must be one of: {', '.join(KINDS)}")
    if not name:
        raise ValueError("Name is required.")
    if not 1 <= share_pct <= 100:
        raise ValueError("Ownership share must be between 1 and 100.")
    with db.connect() as con:
        if con.execute("SELECT 1 FROM accounts WHERE source='manual' AND name=?", (name,)).fetchone():
            raise ValueError(f"A manual account named '{name}' already exists.")
        con.execute("INSERT INTO accounts (external_id, source, kind, name, share_pct, provider) VALUES (?, 'manual', ?, ?, ?, ?)",
                    (f"manual:{name}", kind, name, share_pct, (provider or "").strip() or None))


def save_balance(name: str, amount, as_of: str | None = None) -> tuple[int, str]:
    """Enter amounts as positive numbers; mortgages and cards are stored as negatives (what you owe)."""
    as_of = as_of or date.today().isoformat()
    with db.connect() as con:
        row = con.execute("SELECT id, kind FROM accounts WHERE source='manual' AND name=?", (name,)).fetchone()
        if not row:
            raise ValueError(f"No manual account named '{name}'.")
        pence = abs(db.to_pence(amount)) * (-1 if row["kind"] in LIABILITIES else 1)
        con.execute("INSERT INTO balances (account_id, as_of, balance_pence) VALUES (?,?,?)"
                    " ON CONFLICT(account_id, as_of) DO UPDATE SET balance_pence=excluded.balance_pence",
                    (row["id"], as_of, pence))
    return pence, as_of


def set_share(account_id: int, share_pct: int):
    """Ownership share for any account (e.g. 50 for a joint account)."""
    if not 1 <= share_pct <= 100:
        raise ValueError("Ownership share must be between 1 and 100.")
    with db.connect() as con:
        if not con.execute("UPDATE accounts SET share_pct=? WHERE id=?", (share_pct, account_id)).rowcount:
            raise ValueError("Account not found.")


# ---- CLI wrappers ----

def add(kind: str, name: str):
    try:
        add_account(kind, name)
    except ValueError as e:
        raise SystemExit(str(e))
    print(f"Added manual account '{name}' ({kind}).")


def set_balance(name: str, amount: str, as_of: str | None = None):
    try:
        pence, as_of = save_balance(name, amount, as_of)
    except ValueError as e:
        raise SystemExit(f"{e} Run: manual add <kind> \"{name}\"")
    print(f"{name}: {pence / 100:,.2f} as of {as_of}")


def listing():
    with db.connect() as con:
        for r in con.execute(
            "SELECT a.name, a.kind, (SELECT balance_pence FROM balances b WHERE b.account_id=a.id"
            " ORDER BY as_of DESC LIMIT 1) bal, (SELECT MAX(as_of) FROM balances b WHERE b.account_id=a.id) d"
            " FROM accounts a WHERE a.source='manual' ORDER BY a.kind, a.name"):
            bal = f"{r['bal'] / 100:,.2f}" if r["bal"] is not None else "no balance yet"
            print(f"{r['kind']:9} {r['name']:30} {bal:>14}  {r['d'] or ''}")
