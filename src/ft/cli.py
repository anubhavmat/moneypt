"""Command line entry point.  Usage: python -m ft.cli <command>"""
import sys

from pathlib import Path

from dotenv import load_dotenv

# The project's .env sits one level above "src", the same file the dashboard reads, whichever folder you run from.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from . import crypto, db  # noqa: E402  (after load_dotenv so env vars are set)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd not in ("genkey", "help"):
        db.init()        # creates the database and tables on first use; safe to run every time
    if cmd == "genkey":
        print(crypto.generate_key())
    elif cmd == "init":
        db.init()
        print("Database ready at", db.db_path())
    elif cmd == "connect":
        db.init()
        from . import truelayer
        truelayer.connect(paste=(len(sys.argv) > 2 and sys.argv[2] == "paste"))
    elif cmd == "sync":
        db.init()
        from . import truelayer
        truelayer.sync(days_back=int(sys.argv[2]) if len(sys.argv) > 2 else 90)
    elif cmd == "classify":
        db.init()
        from . import classify
        print(classify.run_all())
    elif cmd == "review":
        db.init()
        from . import classify
        classify.review()
    elif cmd == "rules" and len(sys.argv) == 5 and sys.argv[2] == "add":
        db.init()
        from . import classify
        classify.add_rule(sys.argv[3], sys.argv[4])
    elif cmd == "share" and len(sys.argv) == 4:
        db.init()
        with db.connect() as con:
            n = con.execute("UPDATE accounts SET share_pct=? WHERE name=?", (int(sys.argv[3]), sys.argv[2])).rowcount
        print(f"Updated {n} account(s). Names must match exactly (see `networth`).")
    elif cmd == "networth":
        db.init()
        from . import reports
        reports.net_worth()
    elif cmd == "monthly":
        db.init()
        from . import reports
        reports.monthly(int(sys.argv[2]) if len(sys.argv) > 2 else 3)
    elif cmd == "autocat":
        db.init()
        from . import autoclassify
        sub = sys.argv[2] if len(sys.argv) > 2 else ""
        if sub == "local":
            from . import localdict
            print(localdict.apply())
        elif sub == "preview":
            print(autoclassify.preview())
        elif sub == "check":
            print(autoclassify.check())
        elif sub == "run":
            print(autoclassify.suggest())
        else:
            print("autocat local | autocat preview | autocat check | autocat run   (run only after reading preview)")
    elif cmd == "t212":
        db.init()
        from . import trading212
        trading212.sync()
    elif cmd == "manual":
        from . import manual
        db.init()
        sub = sys.argv[2] if len(sys.argv) > 2 else ""
        if sub == "add" and len(sys.argv) == 5:
            manual.add(sys.argv[3], sys.argv[4])
        elif sub == "set" and len(sys.argv) in (5, 6):
            manual.set_balance(sys.argv[3], sys.argv[4], sys.argv[5] if len(sys.argv) == 6 else None)
        elif sub == "list":
            manual.listing()
        else:
            print('manual add <kind> "<name>" | manual set "<name>" <amount> [YYYY-MM-DD] | manual list')
    elif cmd == "syncall":
        from . import syncall
        r = syncall.run(int(sys.argv[2]) if len(sys.argv) > 2 else 90, say=print)
        ok, bad, info = syncall.summary_lines(r)
        print("\n".join(["", "SYNCED:"] + [f"  {x}" for x in ok] if ok else []))
        print("\n".join(["NEEDS ATTENTION:"] + [f"  {x}" for x in bad] if bad else []))
        print("\n".join(info))
    elif cmd == "providers":
        db.init()
        from . import truelayer
        truelayer.backfill_providers()
    elif cmd == "diag":
        db.init()
        from . import truelayer
        truelayer.diag()
    else:
        print("Commands: genkey | init | connect [paste] | t212 | classify | review | rules add <text> <category> | share "<name>" <pct> | networth | monthly [n] | sync [days_back] | diag | manual ...")


if __name__ == "__main__":
    main()
