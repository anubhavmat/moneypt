"""TrueLayer Data API: consent flow, token refresh, and sync of accounts, cards, balances, transactions.

Endpoint paths are written from memory of TrueLayer's v1 Data API and have not yet been
run against the sandbox - the first sandbox run is the real test.
"""
import http.server
import os
import secrets
import socket
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone

import httpx

from . import db
from .crypto import decrypt, encrypt

SCOPES = "info accounts balance cards transactions offline_access"
HOSTS = {
    "sandbox": ("https://auth.truelayer-sandbox.com", "https://api.truelayer-sandbox.com"),
    "live": ("https://auth.truelayer.com", "https://api.truelayer.com"),
}
CONSENT_DAYS = 180   # UK reconfirmation window; tracked so we can warn before it lapses


def _cfg():
    env = os.getenv("TL_ENV", "sandbox")
    auth, api = HOSTS[env]
    return {
        "auth": auth, "api": api,
        "client_id": os.environ["TL_CLIENT_ID"],
        "client_secret": os.environ["TL_CLIENT_SECRET"],
        "redirect_uri": os.environ["TL_REDIRECT_URI"],
        "providers": os.getenv("TL_PROVIDERS", "").strip(),
    }


# ---------- consent flow ----------

def _parse_pasted(text: str) -> dict:
    """Pull code/state/error out of a pasted redirect, including phone-browser error pages that wrap
    the real address (percent-encoded, with '&amp;' for '&')."""
    import html
    import re
    s = text.strip()
    for _ in range(3):                      # unwrap nested encoding
        s = html.unescape(urllib.parse.unquote(s))
    out = {}
    for key in ("code", "state", "error", "error_description"):
        m = re.search(rf"[?&]{key}=([^&\s]+)", s)
        if m:
            out[key] = m.group(1)
    return out


def _auth_url(c: dict, state: str) -> str:
    params = {
        "response_type": "code", "client_id": c["client_id"], "scope": SCOPES,
        "redirect_uri": c["redirect_uri"], "state": state,
    }
    if c["providers"]:
        params["providers"] = c["providers"]
    return f"{c['auth']}/?{urllib.parse.urlencode(params)}"


def _exchange_and_store(c: dict, code: str) -> int:
    """Swap the one-time code for tokens and store them encrypted as a new connection. Returns the connection id."""
    tok = httpx.post(f"{c['auth']}/connect/token", data={
        "grant_type": "authorization_code", "client_id": c["client_id"],
        "client_secret": c["client_secret"], "redirect_uri": c["redirect_uri"], "code": code,
    }, timeout=30)
    tok.raise_for_status()
    t = tok.json()
    now = datetime.now(timezone.utc)
    with db.connect() as con:
        cur = con.execute(
            "INSERT INTO connections (provider, env, label, access_token_enc, refresh_token_enc,"
            " access_expires_at, consent_expires_at) VALUES ('truelayer', ?, NULL, ?, ?, ?, ?)",
            (os.getenv("TL_ENV", "sandbox"), encrypt(t["access_token"]), encrypt(t["refresh_token"]),
             (now + timedelta(seconds=t.get("expires_in", 3600))).isoformat(),
             (now + timedelta(days=CONSENT_DAYS)).isoformat()))
        return cur.lastrowid


# ---------- connect from the dashboard ----------
# One attempt at a time. State lives here (module level) because the dashboard script reruns on every click.
CONNECT_TIMEOUT_S = 300
_PENDING: dict = {}          # state -> {"result": dict, "done": Event, "server": HTTPServer|None, "started": float}


def cancel_pending():
    """Drop any unfinished attempt and free the listener port."""
    for p in list(_PENDING.values()):
        srv = p.get("server")
        if srv:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:    # noqa: BLE001 - best effort; the port is freed when the process ends anyway
                pass
    _PENDING.clear()


def begin_connect(listen: bool) -> dict:
    """Start a bank connection. Returns {'state','url','redirect_uri'}; the caller sends the user to the url.

    listen=True opens a one-off listener on the redirect address (same as the terminal command) so the bank's answer is
    caught automatically. listen=False is for phone-app banks: the user pastes the final address instead.
    Raises RuntimeError with a plain message if the listener port is taken."""
    c = _cfg()
    cancel_pending()
    state = secrets.token_urlsafe(16)
    pend = {"result": {}, "done": threading.Event(), "server": None, "started": time.time()}
    if listen:
        redirect = urllib.parse.urlparse(c["redirect_uri"])
        host, port = redirect.hostname, redirect.port or 80

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if "code" in q or "error" in q:           # ignore stray requests such as the browser's favicon fetch
                    pend["result"].update({k: v[0] for k, v in q.items()})
                    pend["done"].set()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Connected. You can close this tab and return to MoneyPT.")

            def log_message(self, *a):    # keep the one-time code out of logs
                pass

        class _ExclusiveServer(http.server.HTTPServer):
            allow_reuse_address = False          # on Windows SO_REUSEADDR lets two listeners share a port, so a stale one would go unnoticed

            def server_bind(self):
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):       # Windows: refuse a port someone else already holds
                    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                super().server_bind()

        try:
            srv = _ExclusiveServer((host, port), Handler)
        except OSError as e:
            raise RuntimeError(f"Could not listen on {host}:{port} ({e.strerror}). Is a terminal connect still running? "
                               "Close it, or use the phone-app route.") from e
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        pend["server"] = srv
    _PENDING[state] = pend
    return {"state": state, "url": _auth_url(c, state), "redirect_uri": c["redirect_uri"]}


def _finish(state: str) -> dict:
    p = _PENDING.pop(state, None)
    if p and p.get("server"):
        try:
            p["server"].shutdown()
            p["server"].server_close()
        except Exception:        # noqa: BLE001
            pass
    r = (p or {}).get("result", {})
    if r.get("state") != state:
        return {"status": "error", "message": "The bank's answer did not match this attempt, so it was ignored. Start again."}
    if "error" in r:
        return {"status": "error", "message": f"The bank returned an error ({r.get('error')}). Start again."}
    try:
        return {"status": "done", "connection_id": _exchange_and_store(_cfg(), r["code"])}
    except httpx.HTTPStatusError as e:
        return {"status": "error", "message": f"TrueLayer rejected the one-time code (HTTP {e.response.status_code}). "
                                               "Codes work once and expire within minutes. Start again."}
    except httpx.HTTPError as e:
        return {"status": "error", "message": f"Network problem while finishing ({type(e).__name__}). Start again."}


def poll_connect(state: str) -> dict:
    """{'status': 'waiting'} | {'status': 'done', 'connection_id': n} | {'status': 'error', 'message': str}"""
    p = _PENDING.get(state)
    if p is None:
        return {"status": "error", "message": "This connection attempt is no longer active. Start again."}
    if p["done"].is_set():
        return _finish(state)
    if time.time() - p["started"] > CONNECT_TIMEOUT_S:
        cancel_pending()
        return {"status": "error", "message": "Timed out after 5 minutes. Start again."}
    return {"status": "waiting"}


def submit_pasted(state: str, text: str) -> dict:
    """Phone-app route: finish using the address the user pasted. A bad paste keeps the attempt open for a retry."""
    p = _PENDING.get(state)
    if p is None:
        return {"status": "error", "message": "This connection attempt is no longer active. Start again."}
    parsed = _parse_pasted(text or "")
    if "code" not in parsed and "error" not in parsed:
        return {"status": "waiting", "hint": "No 'code' found in that address. Paste the whole address, including everything after '?'."}
    p["result"].update(parsed)
    p["done"].set()
    return _finish(state)


def connect(paste: bool = False):
    """Open the bank consent page, catch the redirect on localhost, store encrypted tokens.

    paste=True is for banks that approve in a phone app (e.g. Barclays): open the printed URL on the
    phone, approve in the bank app, then copy the full address of the page it lands on (it will fail
    to load - that is expected) and paste it into the terminal.
    """
    c = _cfg()
    state = secrets.token_urlsafe(16)          # CSRF protection
    url = _auth_url(c, state)

    result: dict = {}
    if paste:
        print("1) Send this URL to your phone and open it there (e.g. email or message it to yourself):\n")
        print(url, "\n")
        print("2) Choose the bank, approve in its app. The browser then fails to load a localhost page - expected.")
        print("3) Copy that page's FULL address (starts with", c["redirect_uri"], ") and paste it below.\n")
        result.update(_parse_pasted(input("Redirect URL: ")))
        if "code" not in result and "error" not in result:
            raise SystemExit("No 'code' in that URL. Paste the whole address, including everything after '?'.")
    else:
        redirect = urllib.parse.urlparse(c["redirect_uri"])
        done = threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                result.update({k: v[0] for k, v in q.items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Connected. You can close this tab and return to the terminal.")
                done.set()

            def log_message(self, *a):  # keep the one-time code out of logs
                pass

        server = http.server.HTTPServer((redirect.hostname, redirect.port or 80), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print("Opening browser for bank consent...\nIf nothing opens, paste this URL:\n", url)
        webbrowser.open(url)
        if not done.wait(timeout=300):
            server.shutdown()
            raise SystemExit("Timed out waiting for the bank redirect (5 min).")
        server.shutdown()

    if result.get("state") != state:
        raise SystemExit("State mismatch - aborting (possible CSRF).")
    if "error" in result:
        raise SystemExit(f"Bank returned an error: {result}")

    conn_id = _exchange_and_store(c, result["code"])
    print(f"Stored connection #{conn_id}. Now run: python -m ft.cli sync")


# ---------- tokens ----------

def _access_token(con, conn_row) -> str:
    expires = datetime.fromisoformat(conn_row["access_expires_at"])
    if expires - datetime.now(timezone.utc) > timedelta(minutes=2):
        return decrypt(conn_row["access_token_enc"])
    c = _cfg()
    r = httpx.post(f"{c['auth']}/connect/token", data={
        "grant_type": "refresh_token", "client_id": c["client_id"],
        "client_secret": c["client_secret"], "refresh_token": decrypt(conn_row["refresh_token_enc"]),
    }, timeout=30)
    r.raise_for_status()
    t = r.json()
    new_exp = datetime.now(timezone.utc) + timedelta(seconds=t.get("expires_in", 3600))
    con.execute(
        "UPDATE connections SET access_token_enc=?, refresh_token_enc=?, access_expires_at=? WHERE id=?",
        (encrypt(t["access_token"]), encrypt(t.get("refresh_token") or decrypt(conn_row["refresh_token_enc"])),
         new_exp.isoformat(), conn_row["id"]))
    return t["access_token"]


# ---------- diagnostics ----------

def diag():
    """Show what each live connection exposes. Prints counts/types/status codes only - no tokens or numbers."""
    c = _cfg()
    env = os.getenv("TL_ENV", "sandbox")
    with db.connect() as con, httpx.Client(base_url=c["api"], timeout=30) as client:
        for conn in con.execute("SELECT * FROM connections WHERE provider='truelayer' AND env=?", (env,)):
            token = _access_token(con, conn)
            h = {"Authorization": f"Bearer {token}"}
            print(f"Connection #{conn['id']} ({env})")
            r = client.get("/data/v1/me", headers=h)
            if r.status_code == 200:
                for x in r.json().get("results", []):
                    print("  provider:", x.get("provider", {}).get("display_name"))
            else:
                print("  /me ->", r.status_code)
            for path in ("accounts", "cards"):
                r = client.get(f"/data/v1/{path}", headers=h)
                if r.status_code != 200:
                    print(f"  /{path} -> HTTP {r.status_code}:", r.text[:200])
                    continue
                res = r.json().get("results", [])
                print(f"  /{path} -> {len(res)} returned:",
                      [x.get("account_type") or x.get("card_type") for x in res])


# ---------- provider (bank) names ----------

def provider_name(item: dict | None, me: dict | None = None) -> str | None:
    """Bank name from an account/card object (preferred) or from the /me response. None if neither has one."""
    for src in (item, me):
        name = ((src or {}).get("provider") or {}).get("display_name")
        if name and str(name).strip():
            return str(name).strip()
    return None


def backfill_providers():
    """Fill the bank name on accounts that don't have one, using TrueLayer. Run once for existing data; sync keeps it
    current afterwards. Prints one line per connection and never overwrites a name you set by hand."""
    c = _cfg()
    env = os.getenv("TL_ENV", "sandbox")
    with db.connect() as con, httpx.Client(base_url=c["api"], timeout=30) as client:
        for conn in con.execute("SELECT * FROM connections WHERE provider='truelayer' AND env=?", (env,)).fetchall():
            token = _access_token(con, conn)
            h = {"Authorization": f"Bearer {token}"}
            me = None
            r = client.get("/data/v1/me", headers=h)
            if r.status_code == 200 and r.json().get("results"):
                me = r.json()["results"][0]
            name = provider_name(None, me)
            if not name:
                print(f"Connection #{conn['id']}: TrueLayer gave no provider name (HTTP {r.status_code}); left unchanged.")
                continue
            n = con.execute("UPDATE accounts SET provider=? WHERE connection_id=? AND provider IS NULL",
                            (name, conn["id"])).rowcount
            con.execute("UPDATE connections SET label=? WHERE id=? AND label IS NULL", (name, conn["id"]))
            print(f"Connection #{conn['id']}: {name} -> {n} account(s) updated")


# ---------- sync ----------

def _get(client, path, token, **params):
    r = client.get(path, headers={"Authorization": f"Bearer {token}"}, params=params or None)
    r.raise_for_status()
    return r.json().get("results", [])


def _sync_one(con, client, conn, today, since, say) -> dict:
    """Sync every account and card of ONE connection. Returns a result record; raises on a connection-level failure."""
    token = _access_token(con, conn)
    days_left = (datetime.fromisoformat(conn["consent_expires_at"]) - datetime.now(timezone.utc)).days
    res = {"id": conn["id"], "bank": conn["label"], "accounts": 0, "transactions": 0, "warnings": [], "days_left": days_left}
    if days_left < 14:
        msg = f"consent expires in {days_left} days - reconnect soon"
        res["warnings"].append(msg)
        say(f"WARNING: connection #{conn['id']} {msg}.")
    auth_rejected = 0
    # Cards expose a different shape from accounts.
    for resource, default_kind in (("accounts", "current"), ("cards", "card")):
        # TrueLayer reports card purchases as POSITIVE (debt rising); we store every account with
        # negative = money out of your pocket, so card amounts are flipped at ingest.
        try:
            items = _get(client, f"/data/v1/{resource}", token)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code in (401, 403):
                auth_rejected += 1
            say(f"  {resource}: skipped ({code})")
            res["warnings"].append(f"{resource} skipped (HTTP {code})")
            continue
        for it in items:
            ext = it["account_id"]
            name = it.get("display_name") or it.get("account_number", {}).get("number") or ext
            kind = default_kind
            if resource == "accounts":
                kind = {"TRANSACTION": "current", "SAVINGS": "savings"}.get(it.get("account_type"), "current")
            bank = provider_name(it)
            if bank:
                res["bank"] = res["bank"] or bank
            con.execute(
                "INSERT INTO accounts (connection_id, external_id, source, kind, name, currency, provider)"
                " VALUES (?,?,?,?,?,?,?) ON CONFLICT(source, external_id) DO UPDATE SET name=excluded.name,"
                " provider=COALESCE(accounts.provider, excluded.provider)",
                (conn["id"], ext, "truelayer", kind, name, it.get("currency", "GBP"), bank))
            acc_id = con.execute("SELECT id FROM accounts WHERE source='truelayer' AND external_id=?",
                                 (ext,)).fetchone()["id"]
            _sync_balance(con, client, token, resource, ext, acc_id, kind, today)
            n = _sync_transactions(con, client, token, resource, ext, acc_id, since, today.isoformat(),
                                   sign=-1 if kind == "card" else 1)
            say(f"  {resource}/{name}: {n} transactions")
            res["accounts"] += 1
            res["transactions"] += n
    if auth_rejected and not res["accounts"]:
        # Nothing could be read because the bank rejected our authorisation: that is a failure, not a quiet success.
        raise PermissionError("authorisation rejected")
    return res


def _friendly(e: Exception) -> str:
    """One-line, secret-free reason for a failed connection."""
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code in (400, 401, 403):
            return f"HTTP {code}: authorisation expired or revoked - reconnect this bank"
        return f"HTTP {code} from TrueLayer"
    if isinstance(e, PermissionError):
        return "authorisation expired or revoked - reconnect this bank"
    if isinstance(e, (httpx.TimeoutException, httpx.ConnectError)):
        return f"network problem ({type(e).__name__})"
    if isinstance(e, (ArithmeticError, ValueError, KeyError, TypeError)):
        return f"unexpected data from the bank ({type(e).__name__}); nothing from this bank was saved"
    return f"unexpected error ({type(e).__name__}); nothing from this bank was saved"


def sync_all(days_back: int = 90, say=print) -> dict:
    """Sync every connection independently. One failing bank never stops the rest, and a failed connection is rolled
    back whole (a savepoint), so you never get half a bank. Returns {'results': [...], 'failed': [...]}."""
    c = _cfg()
    today = datetime.now(timezone.utc).date()
    since = (today - timedelta(days=days_back)).isoformat()
    out = {"results": [], "failed": []}
    env = os.getenv("TL_ENV", "sandbox")
    with db.connect() as con, httpx.Client(base_url=c["api"], timeout=60) as client:
        conns = con.execute("SELECT * FROM connections WHERE provider='truelayer' AND env=?", (env,)).fetchall()
        for conn in conns:
            # Refresh the token BEFORE the savepoint and commit it: if the data sync below fails and rolls back, a
            # rotated refresh token must not be lost with it (the old one may already be invalid at the bank).
            try:
                _access_token(con, conn)
                con.commit()
                conn = con.execute("SELECT * FROM connections WHERE id=?", (conn["id"],)).fetchone()
            except Exception as e:             # noqa: BLE001
                why = _friendly(e)
                say(f"  connection #{conn['id']} ({conn['label'] or 'unnamed'}): FAILED - {why}")
                out["failed"].append({"id": conn["id"], "bank": conn["label"], "reason": why})
                continue
            con.execute("SAVEPOINT conn_sync")
            try:
                out["results"].append(_sync_one(con, client, conn, today, since, say))
                con.execute("RELEASE conn_sync")
            except Exception as e:             # noqa: BLE001 - isolate every failure to its own connection
                con.execute("ROLLBACK TO conn_sync")
                con.execute("RELEASE conn_sync")
                why = _friendly(e)
                say(f"  connection #{conn['id']} ({conn['label'] or 'unnamed'}): FAILED - {why}")
                out["failed"].append({"id": conn["id"], "bank": conn["label"], "reason": why})
    return out


def sync(days_back: int = 90):
    """CLI entry point: same behaviour as before, now failure-isolated."""
    env = os.getenv("TL_ENV", "sandbox")
    with db.connect() as con:
        n = con.execute("SELECT COUNT(*) FROM connections WHERE provider='truelayer' AND env=?", (env,)).fetchone()[0]
    if not n:
        raise SystemExit(f"No {env} connections in this database. Run: python -m ft.cli connect")
    sync_all(days_back)


def _sync_balance(con, client, token, resource, ext, acc_id, kind, today):
    rows = _get(client, f"/data/v1/{resource}/{ext}/balance", token)
    if not rows:
        return
    b = rows[0]
    amount = b.get("current") if "current" in b else b.get("available")
    pence = db.to_pence(amount)
    if kind == "card":
        pence = -abs(pence)    # card balance is the amount owed -> liability
    con.execute("INSERT INTO balances (account_id, as_of, balance_pence) VALUES (?,?,?)"
                " ON CONFLICT(account_id, as_of) DO UPDATE SET balance_pence=excluded.balance_pence",
                (acc_id, today.isoformat(), pence))


def _sync_transactions(con, client, token, resource, ext, acc_id, since, until, sign: int = 1) -> int:
    n = 0
    for pending_path, pending in (("transactions", 0), ("transactions/pending", 1)):
        try:
            rows = _get(client, f"/data/v1/{resource}/{ext}/{pending_path}", token, **{"from": since, "to": until})
        except httpx.HTTPStatusError as e:
            # Surface the failure: silently skipping hid missing transactions before.
            print(f"    {pending_path}: HTTP {e.response.status_code} {e.response.text[:200]}")
            continue
        for t in rows:
            con.execute(
                "INSERT INTO transactions (account_id, external_id, posted_at, description, merchant,"
                " amount_pence, pending, provider_category) VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(account_id, external_id) DO UPDATE SET"
                " posted_at=excluded.posted_at, amount_pence=excluded.amount_pence, pending=excluded.pending",
                (acc_id, t.get("normalised_provider_transaction_id") or t["transaction_id"],
                 t["timestamp"], t.get("description"), t.get("merchant_name"),
                 sign * db.to_pence(t["amount"]), pending, t.get("transaction_classification", [None])[0]
                 if t.get("transaction_classification") else None))
            n += 1
    return n
