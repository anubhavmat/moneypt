"""Trading 212 read-only sync: account value (net worth) and open positions.

Verified against docs.trading212.com: GET /api/v0/equity/account/summary (1 req/5s) and
GET /api/v0/equity/positions (1 req/1s), HTTP Basic auth with API key:secret. Only Invest and
Stocks ISA accounts are supported by the API, and each account type needs its own key pair.
Order/dividend history endpoints are NOT used yet (paths unverified).
"""
import base64
import os
import time
from datetime import date

import httpx

from . import db

BASE = "https://live.trading212.com"
# env var prefix -> (account kind, display name). Add a pair of vars per account type you hold.
ACCOUNTS = {
    "T212_ISA": ("isa", "Trading 212 Stocks ISA"),
    "T212_INVEST": ("invest", "Trading 212 Invest"),
    "T212_PARTNER_ISA": ("isa", "Trading 212 Stocks ISA (partner)"),
}

POSITIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    as_of TEXT NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT,
    quantity REAL,
    avg_price REAL,          -- instrument currency
    current_price REAL,      -- instrument currency
    currency TEXT,
    PRIMARY KEY (account_id, as_of, ticker)
);
"""


def _headers(key: str, secret: str) -> dict:
    cred = base64.b64encode(f"{key}:{secret}".encode()).decode()
    return {"Authorization": f"Basic {cred}"}


KEYS_SCHEMA = """
CREATE TABLE IF NOT EXISTS t212_keys (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,         -- display name, e.g. "Partner Stocks ISA"
    kind TEXT NOT NULL,                -- 'isa' or 'invest'
    key_enc TEXT NOT NULL,
    secret_enc TEXT NOT NULL,
    key_tail TEXT NOT NULL,            -- last 4 characters of the key, so you can tell keys apart without showing them
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def _stored_keys():
    """Keys added through the dashboard (encrypted in the database)."""
    from .crypto import decrypt
    with db.connect() as con:
        con.executescript(KEYS_SCHEMA)
        rows = con.execute("SELECT id, name, kind, key_enc, secret_enc FROM t212_keys ORDER BY id").fetchall()
    for r in rows:
        yield f"db:{r['id']}", r["kind"], r["name"], decrypt(r["key_enc"]), decrypt(r["secret_enc"])


def _configured():
    seen = set()
    for prefix, (kind, name) in ACCOUNTS.items():
        key, secret = os.getenv(f"{prefix}_KEY"), os.getenv(f"{prefix}_SECRET")
        if key and secret:
            seen.add(key)
            yield prefix, kind, name, key, secret
    for item in _stored_keys():                    # a key present in both places is synced once
        if item[3] not in seen:
            yield item


def list_stored_keys() -> list[dict]:
    with db.connect() as con:
        con.executescript(KEYS_SCHEMA)
        return [dict(r) for r in con.execute("SELECT id, name, kind, key_tail, created_at FROM t212_keys ORDER BY id")]


def check_key(key: str, secret: str, transport: httpx.BaseTransport | None = None) -> dict:
    """Try the key against Trading 212 (read-only summary call) WITHOUT saving it.
    Returns {'ok': True, 'currency', 'total_pence'} or {'ok': False, 'reason'}."""
    try:
        with httpx.Client(base_url=BASE, timeout=30, transport=transport) as client:
            r = client.get("/api/v0/equity/account/summary", headers=_headers(key.strip(), secret.strip()))
    except (httpx.TimeoutException, httpx.ConnectError):
        return {"ok": False, "reason": "Could not reach Trading 212 (network problem). Try again."}
    if r.status_code != 200:
        return {"ok": False, "reason": {401: "Trading 212 rejected the key or secret (401). Check both were copied in full.",
                                        403: "The key lacks permission (403). It needs read access to account data.",
                                        429: "Trading 212 is rate limiting. Wait a minute and try again."}.get(
            r.status_code, f"Trading 212 returned HTTP {r.status_code}.")}
    s = r.json()
    return {"ok": True, "currency": s.get("currency", "GBP"), "total_pence": db.to_pence(s["totalValue"]), "ext": str(s["id"])}


def add_key(name: str, kind: str, key: str, secret: str, transport: httpx.BaseTransport | None = None) -> dict:
    """Validate then store a key pair encrypted. Refuses a blank or duplicate name, and an account that is already set up."""
    from .crypto import encrypt
    name, key, secret = (name or "").strip(), (key or "").strip(), (secret or "").strip()
    if not name or not key or not secret:
        return {"ok": False, "reason": "Fill in a name, the key and the secret."}
    if kind not in ("isa", "invest"):
        return {"ok": False, "reason": "Pick ISA or Invest."}
    chk = check_key(key, secret, transport)
    if not chk["ok"]:
        return chk
    with db.connect() as con:
        con.executescript(KEYS_SCHEMA)
        if con.execute("SELECT 1 FROM t212_keys WHERE name=?", (name,)).fetchone():
            return {"ok": False, "reason": "A key with that name already exists."}
        # the same Trading 212 account must not be added twice, whether it came from .env or the dashboard
        have = con.execute("SELECT 1 FROM accounts WHERE source='trading212' AND external_id=?", (chk["ext"],)).fetchone()
        env_keys = {os.getenv(f"{prefix}_KEY") for prefix in ACCOUNTS} - {None, ""}
        if have or key in env_keys:
            return {"ok": False, "reason": "That Trading 212 account is already connected."}
        con.execute("INSERT INTO t212_keys (name, kind, key_enc, secret_enc, key_tail) VALUES (?,?,?,?,?)",
                    (name, kind, encrypt(key), encrypt(secret), key[-4:]))
    return {"ok": True, "currency": chk["currency"], "total_pence": chk["total_pence"]}


def remove_key(key_id: int):
    """Stop syncing this key. Already-synced balances and positions stay in the database."""
    with db.connect() as con:
        con.executescript(KEYS_SCHEMA)
        con.execute("DELETE FROM t212_keys WHERE id=?", (int(key_id),))


def configured() -> bool:
    """True if at least one Trading 212 key pair is set in .env."""
    return any(True for _ in _configured())


def sync(transport: httpx.BaseTransport | None = None):
    """CLI entry point: prints progress; stops with a message if no keys are set."""
    if not configured():
        raise SystemExit("No Trading 212 keys set. Fill T212_ISA_KEY/SECRET (and/or T212_INVEST_*) in .env")
    sync_all(transport=transport)


def sync_all(transport: httpx.BaseTransport | None = None, say=print) -> dict:
    """Sync each configured account independently; a failure in one never stops the other.
    Returns {'results': [{'name','total_pence','positions'}], 'failed': [{'name','reason'}]}."""
    print = say    # noqa: A001 - route the progress lines through the caller's logger
    found = list(_configured())
    out = {"results": [], "failed": []}
    if not found:
        return out
    today = date.today().isoformat()
    with db.connect() as con, httpx.Client(base_url=BASE, timeout=30, transport=transport) as client:
        con.executescript(POSITIONS_SCHEMA)
        for i, (prefix, kind, name, key, secret) in enumerate(found):
            if i and transport is None:
                time.sleep(5.1)    # summary limit is 1 req/5s per account; only needed BETWEEN accounts
            con.execute("SAVEPOINT t212_sync")
            try:
                rec = _sync_account(con, client, kind, name, key, secret, today, transport, print)
                con.execute("RELEASE t212_sync")
                (out["results"] if "total_pence" in rec else out["failed"]).append(rec)
            except Exception as e:             # noqa: BLE001 - isolate each account
                con.execute("ROLLBACK TO t212_sync")
                con.execute("RELEASE t212_sync")
                why = ("network problem (" + type(e).__name__ + ")") if isinstance(e, (httpx.TimeoutException, httpx.ConnectError)) \
                    else type(e).__name__
                print(f"{name}: FAILED - {why}")
                out["failed"].append({"name": name, "reason": why})
    return out


def _sync_account(con, client, kind, name, key, secret, today, transport, print) -> dict:
    """One account. Returns {'name','total_pence','positions','warnings'} on success, or {'name','reason'} when the
    account itself could not be read. A positions failure keeps the (valid) balance and adds a warning."""
    h = _headers(key, secret)
    r = client.get("/api/v0/equity/account/summary", headers=h)
    if r.status_code != 200:
        print(f"{name}: summary failed HTTP {r.status_code} {r.text[:200]}")
        reason = {401: "key rejected (401): check the key and secret",
                  403: "key lacks permission (403)", 429: "rate limited - try again in a minute"}.get(
            r.status_code, f"HTTP {r.status_code}")
        return {"name": name, "reason": reason}
    s = r.json()
    ext = str(s["id"])
    con.execute(
        "INSERT INTO accounts (external_id, source, kind, name, currency, provider) VALUES (?,?,?,?,?,'Trading 212')"
        " ON CONFLICT(source, external_id) DO UPDATE SET name=excluded.name, currency=excluded.currency",
        (ext, "trading212", kind, name, s.get("currency", "GBP")))
    acc_id = con.execute("SELECT id FROM accounts WHERE source='trading212' AND external_id=?", (ext,)).fetchone()["id"]
    total = db.to_pence(s["totalValue"])      # cash + investments, in the account currency
    con.execute("INSERT INTO balances (account_id, as_of, balance_pence) VALUES (?,?,?)"
                " ON CONFLICT(account_id, as_of) DO UPDATE SET balance_pence=excluded.balance_pence",
                (acc_id, today, total))
    cash = s.get("cash", {}).get("availableToTrade", 0)
    inv = s.get("investments", {})
    print(f"{name}: total {total / 100:,.2f} {s.get('currency')} "
          f"(cash {cash:,.2f}, invested value {inv.get('currentValue', 0):,.2f}, "
          f"unrealised P/L {inv.get('unrealizedProfitLoss', 0):,.2f})")
    rec = {"name": name, "total_pence": total, "positions": 0, "warnings": []}

    time.sleep(1.1 if transport is None else 0)   # positions limit is 1 req/s
    p = client.get("/api/v0/equity/positions", headers=h)
    if p.status_code != 200:
        print(f"  positions failed HTTP {p.status_code} {p.text[:200]}")
        rec["warnings"].append(f"balance updated, but positions failed (HTTP {p.status_code})")
        return rec
    con.execute("DELETE FROM positions WHERE account_id=? AND as_of=?", (acc_id, today))
    for pos in p.json():
        ins = pos.get("instrument") or {}
        con.execute(
            "INSERT INTO positions (account_id, as_of, ticker, name, quantity, avg_price, current_price, currency)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (acc_id, today, ins.get("ticker") or "UNKNOWN", ins.get("name"), pos.get("quantity"),
             pos.get("averagePricePaid"), pos.get("currentPrice"), ins.get("currency")))
        rec["positions"] += 1
    print(f"  {rec['positions']} open positions")
    return rec
