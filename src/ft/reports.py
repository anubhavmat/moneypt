"""Read-only summaries over the database."""
from . import db


def net_worth():
    """Latest balance per account, grouped into assets and liabilities. Joint accounts count at share_pct."""
    with db.connect() as con:
        rows = con.execute(
            "SELECT a.name, a.kind, a.source, COALESCE(a.share_pct, 100) share,"
            " (SELECT balance_pence FROM balances b WHERE b.account_id=a.id ORDER BY as_of DESC LIMIT 1) bal,"
            " (SELECT MAX(as_of) FROM balances b WHERE b.account_id=a.id) d FROM accounts a ORDER BY a.kind, a.name"
        ).fetchall()
    total = 0
    print(f"{'kind':9} {'account':36} {'balance':>13} {'share':>6} {'counted':>13}  as of")
    for r in rows:
        if r["bal"] is None:
            print(f"{r['kind']:9} {r['name'][:36]:36} {'no balance':>13}")
            continue
        counted = round(r["bal"] * r["share"] / 100)
        total += counted
        print(f"{r['kind']:9} {r['name'][:36]:36} {r['bal'] / 100:>13,.2f} {r['share']:>5}% {counted / 100:>13,.2f}  {r['d']}")
    print(f"{'NET WORTH':46} {'':>13} {'':>6} {total / 100:>13,.2f}")
    print("(Accounts not yet connected or entered are not included.)")


def monthly(months: int = 3):
    """Spend and income per month with transfers and pending items excluded."""
    from . import periods
    sd = periods.get_start_day()
    skip = ["Transfer"] + [n for n in periods.get_not_spend() if n != "Transfer"]
    with db.connect() as con:
        rows = con.execute(
            f"SELECT {periods.sql_period('t.posted_at', sd)} m,"
            " -SUM(CASE WHEN t.amount_pence<0 THEN t.amount_pence ELSE 0 END) spend,"
            " SUM(CASE WHEN t.amount_pence>0 THEN t.amount_pence ELSE 0 END) income, COUNT(*) n"
            " FROM transactions t LEFT JOIN categories c ON c.id=t.category_id"
            " JOIN accounts a ON a.id=t.account_id"
            f" WHERE t.pending=0 AND COALESCE(c.name,'') NOT IN ({','.join('?' * len(skip))})"
            " GROUP BY m ORDER BY m DESC LIMIT ?", (*skip, months)).fetchall()
    print(f"{'month':8} {'spend':>12} {'income':>12} {'net':>12} {'lines':>6}")
    for r in rows:
        print(f"{r['m']:8} {r['spend'] / 100:>12,.2f} {r['income'] / 100:>12,.2f} "
              f"{(r['income'] - r['spend']) / 100:>12,.2f} {r['n']:>6}")
    print(f"Periods start on day {sd} (named by the month they start in). The first and current periods are partial. "
          f"Excluded from totals: {', '.join(skip)}.")
