"""One call that refreshes everything: TrueLayer banks, Trading 212, then re-classifies the new transactions.

Never raises for a bank/network problem - every outcome is in the returned dict so a UI can show it:
  {'truelayer': {...}|None, 'trading212': {...}|None, 'classified': {...}|None, 'errors': [str]}
"""
from . import classify, db


def run(days_back: int = 90, say=lambda s: None) -> dict:
    import os
    out = {"truelayer": None, "trading212": None, "classified": None, "errors": [], "skipped": []}
    db.init()

    with db.connect() as con:
        env = os.getenv("TL_ENV", "sandbox")
        n_conn = con.execute("SELECT COUNT(*) FROM connections WHERE provider='truelayer' AND env=?", (env,)).fetchone()[0]
    if not n_conn:
        out["skipped"].append("TrueLayer: no bank connections yet (run connect first)")
    elif not (os.getenv("TL_CLIENT_ID") and os.getenv("TL_CLIENT_SECRET")):
        out["skipped"].append("TrueLayer: TL_CLIENT_ID / TL_CLIENT_SECRET are not set in .env")
    else:
        try:
            from . import truelayer
            out["truelayer"] = truelayer.sync_all(days_back, say=say)
        except Exception as e:             # noqa: BLE001 - e.g. a missing/invalid encryption key
            out["errors"].append(f"TrueLayer could not start ({type(e).__name__})")

    try:
        from . import trading212
        if trading212.configured():
            out["trading212"] = trading212.sync_all(say=say)
        else:
            out["skipped"].append("Trading 212: no API keys set in .env")
    except Exception as e:                 # noqa: BLE001
        out["errors"].append(f"Trading 212 could not start ({type(e).__name__})")

    try:
        out["classified"] = classify.run_all()
    except Exception as e:                 # noqa: BLE001 - classification failing must not hide a successful sync
        out["errors"].append(f"Sync finished but re-classifying failed ({type(e).__name__})")
    return out


def summary_lines(r: dict) -> tuple[list[str], list[str], list[str]]:
    """(ok_lines, problem_lines, info_lines) in plain English for display."""
    ok, bad, info = [], [], []
    tl = r.get("truelayer")
    if tl:
        for x in tl["results"]:
            tail = f", {x['transactions']} transactions" if x["transactions"] else ""
            ok.append(f"{x['bank'] or 'Connection ' + str(x['id'])}: {x['accounts']} account(s){tail}")
            bad += [f"{x['bank'] or 'Connection ' + str(x['id'])}: {w}" for w in x["warnings"]]
        bad += [f"{f['bank'] or 'Connection ' + str(f['id'])}: {f['reason']}" for f in tl["failed"]]
    t2 = r.get("trading212")
    if t2:
        for x in t2["results"]:
            ok.append(f"{x['name']}: {x['positions']} position(s)")
            bad += [f"{x['name']}: {w}" for w in x["warnings"]]
        bad += [f"{f['name']}: {f['reason']}" for f in t2["failed"]]
    bad += r.get("errors", [])
    info += r.get("skipped", [])
    c = r.get("classified")
    if c and (c.get("known_transfers") or c.get("linked_lines") or c.get("rules_applied")):
        info.append(f"Re-classified: {c.get('rules_applied', 0)} by your rules, {c.get('known_transfers', 0)} known transfers, "
                    f"{c.get('linked_lines', 0)} paired transfer lines")
    return ok, bad, info
