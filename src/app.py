"""Local dashboard.  Run with run_dashboard.bat (or: python -m streamlit run src/app.py).

Demo mode (sidebar toggle, or open /?hide=1): every amount is masked. All money formatting goes through
fmt_money()/fmt_num() and all charts through bar_chart(), so nothing can bypass the switch.
"""
import os
from datetime import date, datetime, timezone
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from ft import classify, data, db, habits, manual, resilience  # noqa: E402
from ft import periods  # noqa: E402
from ft import property as prop  # noqa: E402


@st.cache_data(ttl="12h", show_spinner=False)
def hpi_context(region: str, ptype: str, since: str) -> dict:
    """Index lookups are slow and change monthly, so cache them. Failures are cached too (12h), by design."""
    return prop.price_context(region, ptype, since)

MASK_MONEY = "£x,xxx.xx"
MASK_NUM = "x.xx"

st.set_page_config(page_title="MoneyPT", page_icon="£", layout="wide")


def _refuse_if_exposed():
    """Your financial data must not be reachable from other devices. Stop unless the server is bound to this computer only."""
    from streamlit import config as _cfg
    addr = (_cfg.get_option("server.address") or "").strip()
    if addr not in ("127.0.0.1", "localhost", "::1"):
        st.error("MoneyPT is not starting because the server is open to your network "
                 f"(server.address is {addr or 'unset, meaning every network interface'}). Anyone on the same network could read your "
                 "finances. Start it with:  python run.py", title="Unsafe start")
        st.stop()


_refuse_if_exposed()
db.init()

# ---------- demo-mode switch ----------
if "hide_amounts" not in st.session_state:
    st.session_state.hide_amounts = True        # every new session starts masked; switch the toggle off to see figures
HIDE = st.sidebar.toggle("Demo mode: hide amounts", key="hide_amounts",
                         help="Masks every amount as £x,xxx.xx. Switch off to show real figures.")


def fmt_money(pence) -> str:
    if pence is None or pd.isna(pence):
        return "–"
    if HIDE:
        return ("-" if pence < 0 else "") + MASK_MONEY
    return f"-£{abs(pence) / 100:,.2f}" if pence < 0 else f"£{pence / 100:,.2f}"


def fmt_num(x, places=2) -> str:
    if x is None or pd.isna(x):
        return "–"
    return MASK_NUM if HIDE else f"{x:,.{places}f}"


MONEY_FORMAT = "£ %,.2f"       # one place to change if a browser renders this oddly (documented fallback: "£ %.2f")


def money_col(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Real mode: money columns become NUMBERS in pounds, so the table sorts numerically (the display format comes from
    money_cfg). Demo mode: they become masked text, which carries no order to leak."""
    out = df.copy()
    for c in cols:
        out[c] = out[c].map(fmt_money) if HIDE else out[c] / 100
    return out


def money_cfg(*cols: str, labels: dict[str, str] | None = None) -> dict:
    """column_config for money columns produced by money_col. Empty in demo mode (the cells are text)."""
    if HIDE:
        return {}
    labels = labels or {}
    return {c: st.column_config.NumberColumn(labels.get(c, c), format=MONEY_FORMAT) for c in cols}


def bar_chart(df: pd.DataFrame, x: str, y: str, color: str | None = None, horizontal: bool = False, height: int = 280):
    """y must be in pounds (not pence). In demo mode: no axis values, no tooltips, so figures can't be read off."""
    axis = alt.Axis(labels=not HIDE, ticks=not HIDE, title=None)
    val = alt.Y(f"{y}:Q", axis=axis) if not horizontal else alt.X(f"{y}:Q", axis=axis)
    cat = alt.X(f"{x}:N", title=None, sort=None) if not horizontal else alt.Y(f"{x}:N", title=None, sort="-x")
    enc = {"x": cat, "y": val} if not horizontal else {"x": val, "y": cat}
    if color:
        # Fixed meaning for the two cash-flow series (brand teal / pepper); any other series uses the default palette.
        fixed = {"Income": "#00C3B2", "Spend": "#FF7F3F"}
        names = list(dict.fromkeys(df[color]))
        scale = alt.Scale(domain=names, range=[fixed.get(n, "#1428A0") for n in names]) if set(names) <= set(fixed) else alt.Undefined
        enc["color"] = alt.Color(f"{color}:N", title=None, scale=scale)
        if not horizontal:
            enc["xOffset"] = alt.XOffset(f"{color}:N")
    else:
        enc["color"] = alt.value("#1428A0")      # single-series bars in the brand blue
    enc["tooltip"] = [] if HIDE else [x, y] + ([color] if color else [])
    return alt.Chart(df).mark_bar().encode(**enc).properties(height=height)


# ---------- header / sidebar ----------
st.title("MoneyPT")
if HIDE:
    st.sidebar.warning("Amounts are hidden. Descriptions and account names are still visible.")
conns = data.connections()
for _, c in conns.iterrows():
    left = (datetime.fromisoformat(c["consent_expires_at"]) - datetime.now(timezone.utc)).days
    if left < 30:
        st.sidebar.error(f"Bank connection #{c['id']} consent ends in {left} days. Reconnect it under Connect a bank account.")

def _save_start_day():
    periods.set_start_day(int(st.session_state["start_day_input"]))


START_DAY = periods.get_start_day()
st.sidebar.number_input("Month starts on day", min_value=1, max_value=28, value=START_DAY, step=1, key="start_day_input",
                        on_change=_save_start_day,
                        help="Your budget month runs from this day to the day before it next month, e.g. 21 means "
                             "21 Oct to 20 Nov. Use 1 for calendar months. Max 28 so every month has the day.")


def _save_not_spend():
    periods.set_not_spend(st.session_state["not_spend_input"])


_all_cats = sorted(set(data.category_names()) - {"Transfer", "Uncategorised"} | set(periods.get_not_spend()))
st.sidebar.multiselect("Count as saved, not spent", _all_cats, default=list(periods.get_not_spend()), key="not_spend_input",
                       on_change=_save_not_spend,
                       help="Categories left out of spend, income and trend totals (transfers always are). They stay "
                            "visible in the Transactions tab.")


with st.sidebar:
    st.divider()
    _clicked = st.button("Sync all accounts", icon=":material/sync:", type="primary", width="stretch", key="sync_btn",
                         disabled=HIDE,
                         help="Refreshes every TrueLayer bank and Trading 212, then re-applies your rules and transfer "
                              "detection. Manual accounts are not touched. Disabled in demo mode so bank names and counts "
                              "are not shown.")
    st.number_input("Days of history", min_value=7, max_value=365, value=90, step=1, key="sync_days",
                    help="How far back to fetch transactions. Already-saved transactions are never duplicated.")
    if _clicked and not HIDE:
        from ft import syncall as _run_sa
        with st.spinner("Syncing banks and Trading 212. This can take a minute..."):
            st.session_state["sync_result"] = _run_sa.run(int(st.session_state["sync_days"]))
        st.session_state["sync_at"] = datetime.now().strftime("%d %b %H:%M")
        st.session_state["txn_saves"] = st.session_state.get("txn_saves", 0) + 1   # table rows may have changed
        st.rerun()                      # redraw every tab with the new data
    _r = st.session_state.get("sync_result")
    if _r and not HIDE:
        from ft import syncall as _sa
        _ok, _bad, _info = _sa.summary_lines(_r)
        st.caption(f"Last sync: {st.session_state.get('sync_at', '')}")
        if _ok:
            st.success("\n".join(f"- {x}" for x in _ok), title="Synced")
        if _bad:
            st.warning("\n".join(f"- {x}" for x in _bad), title="Needs attention")
        for _x in _info:
            st.caption(_x)
        if not (_ok or _bad or _info):
            st.info("Nothing to sync yet.")
    if HIDE:
        st.caption("Sync is disabled in demo mode.")


@st.fragment(run_every="2s")
def _connect_waiting(state: str):
    """Polls the one-off listener until the bank redirects back, then finishes and redraws the page."""
    from ft import truelayer as _tl
    r = _tl.poll_connect(state)
    if r["status"] == "waiting":
        st.info("Waiting for the bank. Approve there, then come back; this updates by itself.")
        return
    st.session_state.pop("connect_state", None)
    st.session_state["connect_result"] = r
    st.rerun()


def _connect_panel():
    from ft import truelayer as _tl
    if not (os.getenv("TL_CLIENT_ID") and os.getenv("TL_CLIENT_SECRET") and os.getenv("TL_REDIRECT_URI")):
        st.caption("Bank connections are not set up (TL_CLIENT_ID, TL_CLIENT_SECRET and TL_REDIRECT_URI in .env).")
        return
    res = st.session_state.get("connect_result")
    if res:
        if res["status"] == "done":
            st.success("Bank connected. Click Sync all accounts to load its accounts.", title="Connected")
        else:
            st.error(res["message"], title="Not connected")
        if st.button("Dismiss", key="connect_dismiss"):
            st.session_state.pop("connect_result", None)
            st.rerun()
    state = st.session_state.get("connect_state")
    if state:
        mode = st.session_state.get("connect_mode", "auto")
        st.link_button("1. Open the bank's consent page", st.session_state["connect_url"], width="stretch")
        if mode == "auto":
            _connect_waiting(state)
        else:
            st.caption("2. Approve in the bank's app. The page that opens afterwards fails to load, which is expected. "
                       "3. Copy its full address and paste it here.")
            pasted = st.text_input("Address of the page that failed to load", key="connect_paste")
            if st.button("Finish connecting", key="connect_finish", disabled=not pasted):
                r = _tl.submit_pasted(state, pasted)
                if r["status"] == "waiting":
                    st.warning(r.get("hint", "Could not read that address."))
                else:
                    st.session_state.pop("connect_state", None)
                    st.session_state["connect_result"] = r
                    st.rerun()
        if st.button("Cancel", key="connect_cancel"):
            _tl.cancel_pending()
            st.session_state.pop("connect_state", None)
            st.rerun()
        return
    st.caption("Whoever owns the account must log in at their bank and approve. Access is read-only. Consent lasts 180 days.")
    route = st.radio("How will they approve?", ["On this computer", "In the bank's phone app"], key="connect_route",
                     help="Pick the phone-app route for banks that ask you to approve in their mobile app (e.g. Barclays).")
    if st.button("Start connecting", key="connect_start", width="stretch"):
        try:
            a = _tl.begin_connect(listen=(route == "On this computer"))
        except RuntimeError as e:
            st.session_state["connect_result"] = {"status": "error", "message": str(e)}
        except Exception as e:                 # noqa: BLE001 - e.g. a missing .env value; never show a traceback with config
            st.session_state["connect_result"] = {"status": "error", "message": f"Could not start ({type(e).__name__})."}
        else:
            st.session_state.update(connect_state=a["state"], connect_url=a["url"],
                                    connect_mode="auto" if route == "On this computer" else "paste")
            st.session_state.pop("connect_result", None)
        st.rerun()


def _t212_panel():
    from ft import trading212 as _t
    note = st.session_state.pop("t212_note", None)
    if note:
        (st.success if note["ok"] else st.error)(note["text"])
    st.caption("Each Trading 212 login has its own key. In the Trading 212 app: Settings, API, create a read-only key "
               "(account data only, never trading). Paste the key and secret here; they are stored encrypted and never shown again.")
    with st.form("t212_add", clear_on_submit=True):
        name = st.text_input("Name for this account", placeholder="Partner Stocks ISA")
        kind = st.radio("Account type", ["ISA", "Invest"], horizontal=True)
        key = st.text_input("API key", type="password")
        secret = st.text_input("API secret", type="password")
        if st.form_submit_button("Test and save", width="stretch"):
            with st.spinner("Checking the key with Trading 212..."):
                r = _t.add_key(name, kind.lower(), key, secret)
            if r["ok"]:
                st.session_state["t212_note"] = {"ok": True, "text": "Key works and is saved. Click Sync all accounts to load it."}
            else:
                st.session_state["t212_note"] = {"ok": False, "text": r["reason"]}
            st.rerun()
    stored = _t.list_stored_keys()
    if stored:
        st.caption("Keys added here")
        for k in stored:
            c1, c2 = st.columns([3, 1], vertical_alignment="center")
            c1.markdown(f"**{k['name']}**  \n{k['kind'].upper()} · key ending {k['key_tail']}")
            if c2.button("Remove", key=f"t212_rm_{k['id']}", help="Stops syncing. Balances already loaded stay."):
                _t.remove_key(k["id"])
                st.rerun()


with st.sidebar:
    with st.expander("Connect a bank account", expanded=bool(st.session_state.get("connect_state"))):
        if HIDE:
            st.caption("Connecting is disabled in demo mode.")
        else:
            _connect_panel()
    with st.expander("Add a Trading 212 account", expanded=False):
        if HIDE:
            st.caption("Adding accounts is disabled in demo mode.")
        else:
            _t212_panel()
_span = data.data_span()


def plabel(key: str) -> str:
    """Readable period name, with a marker when the synced data doesn't cover the whole period."""
    txt = periods.label(key, START_DAY)
    return txt + " · partial" if _span and periods.is_partial(key, START_DAY, _span[0], _span[1]) else txt


st.sidebar.caption(f"Database: {db.db_path()}")

tab_over, tab_spend, tab_habits, tab_prot, tab_txn, tab_auto, tab_hold, tab_manual = st.tabs(
    ["Overview", "Spending", "Habits", "Protection & growth", "Transactions", "Auto-categorise", "Holdings", "Manual accounts & ownership"])

# ---------- overview ----------
with tab_over:
    acc = data.accounts_latest()
    acc["counted"] = (acc["balance_pence"] * acc["share_pct"] / 100).round()
    have = acc.dropna(subset=["balance_pence"])
    assets = have.loc[have["counted"] > 0, "counted"].sum()
    liabs = have.loc[have["counted"] < 0, "counted"].sum()
    c1, c2, c3 = st.columns(3)
    c1.metric("Net worth", fmt_money(have["counted"].sum()))
    c2.metric("Assets", fmt_money(assets))
    c3.metric("Liabilities", fmt_money(liabs))
    if len(have) < len(acc):
        st.caption(f"{len(acc) - len(have)} account(s) have no balance yet.")
    st.caption("Counts each account at your ownership share. Accounts not connected or entered are not included.")

    eq = prop.equity()
    if eq:
        with st.container(border=True):
            st.markdown(f"**Property equity** · {eq['house']}")
            e1, e2, e3, e4 = st.columns(4)
            e1.metric("Your valuation", fmt_money(eq["value_pence"]), border=True, height="stretch")
            e2.metric("Mortgage", fmt_money(-eq["mortgage_pence"]), border=True, height="stretch")
            e3.metric("Equity", fmt_money(eq["equity_pence"]), border=True, height="stretch")
            e4.metric("Loan to value", "xx.x%" if HIDE else (f"{eq['ltv_pct']}%" if eq["ltv_pct"] is not None else "–"),
                      border=True, height="stretch")
            st.caption(f"Valuation entered {eq['value_as_of']}; mortgage balance {eq['mortgage_as_of'] or 'not set'}. "
                       "Equity uses your own valuation, not an estimate.")
            if eq["stale_gap_days"] > 45:
                st.warning(f"The valuation and mortgage balance are {eq['stale_gap_days']} days apart, so equity may be off.",
                           title="Out-of-step dates")
            ctx = hpi_context(eq["region"], eq["type"], eq["value_as_of"][:7])
            if ctx["ok"]:
                pct = ctx["change_pct"]
                st.caption(f"Context: average {ctx['type']} prices in {ctx['region'].title()} moved "
                           f"{'x.x' if HIDE else f'{pct:+.1f}'}% from {ctx['from_month']} to {ctx['to_month']} "
                           "(UK House Price Index, a regional average, not your house). Not added to your valuation.")
            else:
                st.caption(f"Price index context unavailable: {ctx['reason']}")

    by_kind = have.groupby("kind", as_index=False)["counted"].sum()
    by_kind["pounds"] = by_kind["counted"] / 100
    left_col, right_col = st.columns([1, 1])
    with left_col:
        st.subheader("By account type")
        if by_kind.empty:
            st.info("Nothing to chart yet. Add a manual account in the last tab, or connect a bank or Trading 212 from the sidebar.")
        else:
            st.altair_chart(bar_chart(by_kind, "kind", "pounds"), width="stretch")
    with right_col:
        st.subheader("Accounts")
        show = acc.rename(columns={"name": "Account", "provider": "Provider", "kind": "Type", "share_pct": "Share %",
                                   "as_of": "As of"})
        show = money_col(show.rename(columns={"balance_pence": "Balance", "counted": "Counted"}), ["Balance", "Counted"])
        st.dataframe(show[["Provider", "Account", "Type", "Balance", "Share %", "Counted", "As of"]],
                     hide_index=True, width="stretch", column_config=money_cfg("Balance", "Counted"),
                     alt="Accounts with provider, balance, your share and the amount counted in net worth")
        if (acc["provider"] == "").any():
            st.caption(f"{int((acc['provider'] == '').sum())} account(s) have no provider yet. Set them in "
                       "Manual accounts & ownership → Provider (bank).")

    st.subheader("Cash flow by budget month (transfers excluded)")
    cf = data.monthly_cashflow(12).sort_values("month")
    if cf.empty:
        st.info("No transactions yet.")
    else:
        cf["period"] = cf["month"].map(plabel)
        long = pd.concat([
            pd.DataFrame({"period": cf["period"], "series": "Spend", "pounds": cf["spend_pence"] / 100}),
            pd.DataFrame({"period": cf["period"], "series": "Income", "pounds": cf["income_pence"] / 100})])
        st.altair_chart(bar_chart(long, "period", "pounds", color="series"), width="stretch")
        t = cf.copy()
        t["net_pence"] = t["income_pence"] - t["spend_pence"]
        t = money_col(t, ["spend_pence", "income_pence", "net_pence"]).rename(
            columns={"spend_pence": "Spend", "income_pence": "Income", "net_pence": "Net", "period": "Period", "lines": "Lines"})
        st.dataframe(t[["Period", "Spend", "Income", "Net", "Lines"]].iloc[::-1], hide_index=True, width="stretch",
                     column_config=money_cfg("Spend", "Income", "Net"), alt="Spend, income and net for each budget month")
        st.caption(f"Months start on day {START_DAY}. Periods marked partial are not fully covered by the synced data "
                   "(the first one starts before your history, the current one has not finished), so they look low.")

# ---------- spending ----------
with tab_spend:
    ms = data.months()
    if not ms:
        st.info("No transactions yet.")
    else:
        st.subheader("Trends by category")
        DEFAULT_WINDOW = {"Daily": 30, "Weekly": 12, "Monthly": 6, "Quarterly": 4, "Annual": 3}
        MAX_WINDOW = {"Daily": 180, "Weekly": 104, "Monthly": 36, "Quarterly": 16, "Annual": 10}
        c1, c2, c3 = st.columns([2.2, 1, 1.8], vertical_alignment="bottom")
        grain = c1.segmented_control("Time period", list(data.GRAINS), default="Monthly", key="trend_grain") or "Monthly"
        UNIT = {"Daily": "days", "Weekly": "weeks", "Monthly": "months", "Quarterly": "quarters", "Annual": "years"}
        n_win = c2.number_input(f"Last N {UNIT[grain]}", min_value=2, max_value=MAX_WINDOW[grain],
                                value=DEFAULT_WINDOW[grain], step=1, key=f"trend_n_{grain}")
        skip_big = c3.toggle("Leave out Mortgage payment and Housing", value=True, key="trend_skip",
                             help="These are large and lumpy, so they hide movement in everyday categories. "
                                  "Savings are already left out (see the sidebar).")
        excl = ("Mortgage payment", "Housing") if skip_big else ()
        full = data.spend_by_grain(grain, exclude=excl)
        if full.empty:
            st.info("Nothing to chart for those settings.")
        else:
            # Category choices come from ALL history (not the window) so the selection stays valid when the grain changes.
            cat_order = (data.spend_by_grain("Annual", exclude=excl).groupby("category")["spend_pence"].sum()
                         .sort_values(ascending=False).index.tolist())
            st.session_state["trend_cats"] = [c for c in st.session_state.get("trend_cats", cat_order[:5]) if c in cat_order]
            chosen = st.multiselect("Categories to chart (pick one to focus on it)", cat_order, key="trend_cats")

            # Window: the last N buckets. Daily and weekly are padded so days/weeks with no spend show as zero, not gaps.
            labels = full.drop_duplicates("bucket").set_index("bucket")["label"]
            if grain == "Daily":
                last = pd.Timestamp(full["bucket"].max())
                buckets = [d.strftime("%Y-%m-%d") for d in pd.date_range(end=last, periods=int(n_win), freq="D")]
                labels = pd.Series({b: pd.Timestamp(b).strftime("%d %b") for b in buckets})
            elif grain == "Weekly":
                last = pd.Timestamp(full["bucket"].max())
                buckets = [d.strftime("%Y-%m-%d") for d in pd.date_range(end=last, periods=int(n_win), freq="7D")]
                labels = pd.Series({b: "w/c " + pd.Timestamp(b).strftime("%d %b") for b in buckets})
            else:
                buckets = sorted(full["bucket"].unique())[-int(n_win):]
            if grain == "Monthly":
                labels = pd.Series({b: plabel(b) for b in buckets})        # keeps the 'partial' marker on incomplete months
            win = full[full["bucket"].isin(buckets)]
            piv = win.pivot_table(index="category", columns="bucket", values="spend_pence", aggfunc="sum", fill_value=0)
            piv = piv.reindex(columns=buckets, fill_value=0)
            piv = piv.reindex([c for c in cat_order if c in piv.index])
            col_labels = [labels[b] for b in buckets]

            if not chosen:
                st.info("Pick at least one category to chart.")
            else:
                sel = piv.reindex([c for c in chosen if c in piv.index], fill_value=0)
                long = sel.reset_index().melt(id_vars="category", var_name="bucket", value_name="spend_pence")
                long["period"] = long["bucket"].map(labels)
                long["pounds"] = long["spend_pence"] / 100
                if len(chosen) == 1:
                    chart = alt.Chart(long).mark_bar(color="#1428A0", cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
                        x=alt.X("period:N", sort=col_labels, title=None, axis=alt.Axis(labelAngle=-45 if len(buckets) > 8 else -30)),
                        y=alt.Y("pounds:Q", axis=alt.Axis(labels=not HIDE, ticks=not HIDE, title=None)),
                        tooltip=[] if HIDE else ["period", "category", "pounds"]).properties(height=300)
                else:
                    chart = alt.Chart(long).mark_line(point=len(buckets) <= 40).encode(
                        x=alt.X("period:N", sort=col_labels, title=None, axis=alt.Axis(labelAngle=-45 if len(buckets) > 8 else -30)),
                        y=alt.Y("pounds:Q", axis=alt.Axis(labels=not HIDE, ticks=not HIDE, title=None)),
                        color=alt.Color("category:N", title=None, scale=alt.Scale(
                            range=["#1428A0", "#00C3B2", "#FF7F3F", "#0077C8", "#E33B9A", "#75787B", "#069BAE", "#1347A9"])),
                        tooltip=[] if HIDE else ["period", "category", "pounds"]).properties(height=300)
                st.altair_chart(chart, width="stretch")

            tbl = piv.copy()
            tbl.columns = col_labels
            chg = [None if (len(buckets) < 2 or piv.iloc[i, -2] == 0) else (piv.iloc[i, -1] / piv.iloc[i, -2] - 1) * 100
                   for i in range(len(piv))]
            shown_t = money_col(tbl, col_labels)
            # Real mode: a NUMBER (percent), so it sorts correctly. Demo mode: masked text that carries no order.
            shown_t["Change vs previous"] = ["x%" if (HIDE and v is not None) else ("–" if HIDE else v) for v in chg]
            st.dataframe(shown_t.reset_index().rename(columns={"category": "Category"}), hide_index=True, width="stretch",
                         column_config={**money_cfg(*col_labels),
                                        **({} if HIDE else {"Change vs previous": st.column_config.NumberColumn(
                                            "Change vs previous", format="%+.0f%%")})},
                         alt=f"{grain} spend per category")
            st.caption("'Change vs previous' compares the last two periods shown, and the latest is usually still in "
                       "progress, so it tends to look like a fall. Monthly uses your budget month; quarters and years group "
                       "budget months by the month they start in. Weeks start on Monday.")
        st.divider()
        month = st.selectbox("Period", ms, key="spend_month", format_func=plabel)
        cat = data.category_spend(month)
        a, b = st.columns(2)
        with a:
            st.subheader("By category")
            cat["pounds"] = cat["spend_pence"] / 100
            st.altair_chart(bar_chart(cat, "category", "pounds", horizontal=True), width="stretch")
            st.dataframe(money_col(cat, ["spend_pence"])[["category", "spend_pence", "lines"]].rename(
                columns={"category": "Category", "spend_pence": "Spend", "lines": "Lines"}),
                hide_index=True, width="stretch", column_config=money_cfg("Spend"),
                alt="Spend by category for the selected period")
        with b:
            st.subheader("Top merchants")
            tm = data.top_merchants(month)
            tm["pounds"] = tm["spend_pence"] / 100
            st.altair_chart(bar_chart(tm, "merchant", "pounds", horizontal=True), width="stretch")
        if cat["category"].eq("Uncategorised").all():
            st.info("Everything is Uncategorised so far. Recategorise in the Transactions tab and your rules build up.")

# ---------- habits: fixed vs flexible, watch-list ----------
with tab_habits:
    if not data.months():
        st.info("No transactions yet.")
    else:
        st.subheader("Fixed vs flexible spend")
        st.caption("Fixed = bills you can't change quickly. Flexible = everything you choose. Flexible is where habits show.")
        _cats = sorted(set(data.category_names()) - {"Transfer", "Uncategorised"} - set(periods.get_not_spend()))
        _fx = [c for c in habits.get_fixed() if c in _cats]
        _pick = st.multiselect("Categories that count as fixed", _cats, default=_fx, key="fixed_pick")
        if sorted(_pick) != sorted(_fx):
            habits.set_fixed(_pick)
            st.rerun()
        ff = habits.fixed_flexible(12)
        ff["label"] = ff["month"].map(plabel)
        if len(ff) == 0:
            st.info("Nothing to show yet.")
        else:
            cur_row = ff.iloc[-1]
            m1, m2, m3 = st.columns(3)
            m1.metric(f"Fixed ({cur_row['label'].split(' (')[0]})", fmt_money(cur_row["fixed_pence"]))
            m2.metric("Flexible", fmt_money(cur_row["flexible_pence"]))
            m3.metric("Flexible share of spend", "xx%" if HIDE or pd.isna(cur_row["flexible_share"]) else f"{cur_row['flexible_share']:.0f}%")
            ff["short"] = ff["month"].map(lambda k: plabel(k).split(" (")[0])
            long = ff.melt(id_vars=["short"], value_vars=["fixed_pence", "flexible_pence"], var_name="type", value_name="pence")
            long["type"] = long["type"].map({"fixed_pence": "Fixed", "flexible_pence": "Flexible"})
            long["pounds"] = long["pence"] / 100
            chart = alt.Chart(long).mark_bar().encode(
                x=alt.X("short:N", sort=list(ff["short"]), title=None, axis=alt.Axis(labelAngle=0)),
                y=alt.Y("pounds:Q", axis=alt.Axis(labels=not HIDE, ticks=not HIDE, title=None)),
                color=alt.Color("type:N", title=None, scale=alt.Scale(domain=["Fixed", "Flexible"], range=["#1428A0", "#FF7F3F"])),
                tooltip=[] if HIDE else ["short", "type", "pounds"]).properties(height=280)
            st.altair_chart(chart, width="stretch")
            t = money_col(ff, ["income_pence", "fixed_pence", "flexible_pence", "left_pence"])
            t["flexible_share"] = ["xx%" for _ in range(len(t))] if HIDE else t["flexible_share"]
            st.dataframe(t[["label", "income_pence", "fixed_pence", "flexible_pence", "flexible_share", "left_pence"]],
                         hide_index=True, width="stretch", alt="Fixed and flexible spend per budget month",
                         column_config={**money_cfg("income_pence", "fixed_pence", "flexible_pence", "left_pence", labels={
                             "income_pence": "Income", "fixed_pence": "Fixed", "flexible_pence": "Flexible", "left_pence": "Left over"}),
                             "label": "Period",
                             **({} if HIDE else {"flexible_share": st.column_config.NumberColumn("Flexible %", format="%.0f%%")})})
            st.caption("Left over = income minus fixed and flexible spend. Savings and investing are not counted as spend. "
                       "The latest period is usually still in progress.")

        st.divider()
        st.subheader("Watch-list")
        st.caption("Track merchants you want to control. The pattern is matched anywhere in the transaction description, "
                   "ignoring case (e.g. 'mcdonald' catches MCDONALDS and MCDONALD'S).")
        with st.form("watch_add", clear_on_submit=True):
            f1, f2, f3 = st.columns([1.2, 1.2, 1])
            w_label = f1.text_input("Name", placeholder="McDonald's")
            w_pat = f2.text_input("Match text", placeholder="mcdonald")
            w_lim = f3.number_input("Monthly limit £ (0 = none)", min_value=0.0, step=5.0, value=0.0)
            if st.form_submit_button("Add to watch-list"):
                try:
                    habits.add_watch(w_label, w_pat, int(round(w_lim * 100)) if w_lim > 0 else None)
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
        wl = habits.watchlist()
        if wl.empty:
            st.info("Nothing on the watch-list yet. Add McDonald's, Temu, a delivery app...")
        else:
            wm = habits.watch_by_month(6)
            months_seen = list(dict.fromkeys(wm["month"]))
            for _, w in wl.iterrows():
                rows = wm[wm["label"] == w["label"]]
                this = rows[rows["month"] == months_seen[-1]].iloc[0]
                lim = w["monthly_limit_pence"]
                over = pd.notna(lim) and this["spend_pence"] > lim
                c1, c2, c3, c4 = st.columns([2, 1.4, 1.4, 0.6], vertical_alignment="center")
                c1.markdown(f"**{w['label']}**\n\n`{w['pattern']}`")
                c2.metric(plabel(months_seen[-1]).split(" (")[0], fmt_money(this["spend_pence"]),
                          delta=None if pd.isna(lim) else ("over limit" if over else "within limit"),
                          delta_color="inverse" if over else "normal", delta_arrow="off")
                c3.metric(f"{len(rows)}-period total", fmt_money(rows["spend_pence"].sum()),
                          help="Sum over the periods shown, including the one in progress.")
                if c4.button("Remove", key=f"wl_rm_{w['id']}"):
                    habits.remove_watch(int(w["id"]))
                    st.rerun()
            wm_chart = wm.copy()
            wm_chart["period"] = wm_chart["month"].map(lambda k: plabel(k).split(" (")[0])
            wm_chart["pounds"] = wm_chart["spend_pence"] / 100
            st.altair_chart(alt.Chart(wm_chart).mark_bar().encode(
                x=alt.X("period:N", sort=[plabel(m).split(" (")[0] for m in months_seen], title=None, axis=alt.Axis(labelAngle=0)),
                xOffset="label:N", y=alt.Y("pounds:Q", axis=alt.Axis(labels=not HIDE, ticks=not HIDE, title=None)),
                color=alt.Color("label:N", title=None, scale=alt.Scale(range=["#1428A0", "#00C3B2", "#FF7F3F", "#0077C8", "#E33B9A", "#75787B"])),
                tooltip=[] if HIDE else ["period", "label", "pounds"]).properties(height=260), width="stretch")

# ---------- protection & growth ----------
with tab_prot:
    st.subheader("Short-term protection: months of cover")
    cv = resilience.cover(12)
    if not cv["ok"]:
        st.info("Not enough complete months of data yet to work out cover.")
    else:
        k1, k2, k3 = st.columns(3)
        k1.metric("Liquid cash", fmt_money(cv["cash_pence"]), help="Current + savings accounts, less card balances, at your share.")
        k2.metric("Cover: fixed bills only", "x.x months" if HIDE else f"{cv['cover_fixed']:.1f} months",
                  help="Liquid cash / average monthly fixed spend. How long you could pay the essentials with no income.")
        k3.metric("Cover: all spending", "x.x months" if HIDE else f"{cv['cover_total']:.1f} months",
                  help="Liquid cash / average monthly fixed + flexible spend.")
        if cv["months_used"] < 3:
            st.warning(f"These averages use only {cv['months_used']} complete budget month(s). A month counts as complete only "
                       "when every connected account has transactions from its first day. Treat the figures as rough until "
                       "there are 3 or more.", title="Short history")
        st.caption("Savings in ISAs, pensions and property are not counted as liquid. A common target is 3 to 6 months of "
                   "essentials; with income protection insurance you can reasonably hold less.")

    st.divider()
    st.subheader("Long-term growth")
    sr = resilience.savings_rate(12)
    g1, g2 = st.columns(2)
    with g1:
        st.markdown("**Savings rate** (saved / income, per complete month)")
        if sr.empty:
            st.info("No complete month yet.")
        else:
            sr["period"] = sr["month"].map(lambda k: plabel(k).split(" (")[0])
            ch = alt.Chart(sr).mark_bar(color="#00C3B2").encode(
                x=alt.X("period:N", sort=list(sr["period"]), title=None, axis=alt.Axis(labelAngle=0)),
                y=alt.Y("rate_pct:Q", axis=alt.Axis(labels=not HIDE, ticks=not HIDE, title=None, format=".0f")),
                tooltip=[] if HIDE else ["period", alt.Tooltip("rate_pct:Q", format=".1f")]).properties(height=240)
            st.altair_chart(ch, width="stretch")
            st.caption("Counts what went into your 'saved, not spent' categories (sidebar). Lumpy: one big top-up moves it a lot.")
    with g2:
        st.markdown("**Net worth over time**")
        nw = resilience.net_worth_history()
        if len(nw) < 2:
            st.info("Needs at least two sync days. It builds up each time you sync.")
        else:
            nw["pounds"] = nw["net_pence"] / 100
            nw["date"] = pd.to_datetime(nw["as_of"])
            ch = alt.Chart(nw).mark_line(point=True, color="#1428A0").encode(
                x=alt.X("date:T", title=None),
                y=alt.Y("pounds:Q", scale=alt.Scale(zero=False), axis=alt.Axis(labels=not HIDE, ticks=not HIDE, title=None)),
                tooltip=[] if HIDE else [alt.Tooltip("date:T"), alt.Tooltip("pounds:Q", format=",.0f"), "accounts"]).properties(height=240)
            st.altair_chart(ch, width="stretch")
            st.caption(f"{len(nw)} snapshots from {nw['as_of'].iloc[0]}. Uses each account's latest balance on or before each "
                       "date, starting once every account had a balance. Manual valuations change only when you update them.")

    st.divider()
    st.subheader("Remortgage readiness")
    eq2 = prop.equity()
    if not eq2:
        st.info("Set up the house and mortgage in Manual accounts & ownership to see this.")
    else:
        terms = resilience.get_mortgage_terms()
        with st.form("mortgage_terms"):
            f1, f2 = st.columns(2)
            rate_in = f1.number_input("Current interest rate (%)", min_value=0.0, max_value=25.0, step=0.05,
                                      value=float(terms.get("rate_pct") or 0.0), format="%.2f")
            fe = terms.get("fix_end")
            fix_in = f2.date_input("Fixed rate ends", value=date.fromisoformat(fe) if fe else None, format="YYYY-MM-DD")
            if st.form_submit_button("Save mortgage terms"):
                try:
                    resilience.set_mortgage_terms(rate_in if rate_in > 0 else None, fix_in)
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
        hd = resilience.ltv_headroom(eq2["value_pence"], eq2["mortgage_pence"])
        r1, r2, r3 = st.columns(3)
        r1.metric("Loan to value", "xx.x%" if HIDE else f"{hd['ltv']:.1f}%")
        r2.metric("Lender band", "xx%" if HIDE else (f"up to {hd['band']}%" if hd["band"] else "above 95%"))
        if hd["next_band"] is not None:
            r3.metric(f"Paydown to reach {hd['next_band']}% band", fmt_money(hd["paydown_pence"]),
                      help="Mortgage reduction (or value increase) needed to drop into the next cheaper band.")
        if terms.get("fix_end"):
            days = (date.fromisoformat(terms["fix_end"]) - date.today()).days
            if days < 0:
                st.error("The fixed period has ended. You are likely on the lender's standard variable rate.")
            elif days <= 180:
                st.warning(f"Fixed rate ends in {days} days. Most lenders let you lock a new deal up to 6 months ahead.",
                           title="Remortgage window")
            else:
                st.caption(f"Fixed rate ends in {days} days.")
        if terms.get("rate_pct"):
            rc = resilience.rate_cost(eq2["mortgage_pence"], terms["rate_pct"])
            q1, q2 = st.columns(2)
            q1.metric("Interest per year at current rate (approx.)", fmt_money(rc["annual_interest_pence"]))
            q2.metric("Extra per year if rate rises 1 point (approx.)", fmt_money(rc["extra_per_year_pence"]))
        st.caption("Band edges and rates vary by lender, and valuation here is your own figure. Interest figures ignore "
                   "repayment of principal, so they are a rough size of the exposure, not a quote.")

# ---------- transactions + recategorise ----------
with tab_txn:
    ms = data.months()
    accounts_all = data.accounts_latest()["name"].tolist()
    f1, f2, f3, f4 = st.columns([1, 1.4, 1.2, 1.4])
    month = f1.selectbox("Period", ["All"] + ms, key="txn_month", format_func=lambda k: k if k == "All" else plabel(k))
    account = f2.selectbox("Account", ["All"] + accounts_all, key="txn_acc")
    cats = ["All"] + data.category_names() + ["Uncategorised"]
    category = f3.selectbox("Category", list(dict.fromkeys(cats)), key="txn_cat")
    text = f4.text_input("Description contains", key="txn_text")
    inc_t = st.checkbox("Include transfers", value=False, key="txn_transfers")
    with st.expander("Set a standing category for a merchant"):
        st.caption("Every transaction containing this text becomes the chosen category, and future syncs follow it. "
                   "Lines you already set to a different category by hand are left alone.")
        d1, d2, d3 = st.columns([1.4, 1.2, 0.6], vertical_alignment="bottom")
        d_text = d1.text_input("Description contains", key="decide_text", placeholder="e.g. TESCO")
        d_cat = d2.selectbox("Category", sorted(set(data.category_names()) - {"Uncategorised", "Transfer"}
                                                | {"Mortgage payment", "Savings & investing"}), key="decide_cat")
        if d3.button("Apply", key="decide_go"):
            try:
                r = classify.decide_merchant(d_text, d_cat)
                st.session_state["txn_saves"] = st.session_state.get("txn_saves", 0) + 1
                st.session_state["flash"] = ", ".join(f"{k}: {v}" for k, v in r.items())
                st.rerun()
            except ValueError as e:
                st.error(str(e))
    tx = data.transactions(None if month == "All" else month, None if account == "All" else account,
                           None if category == "All" else category, text, inc_t)
    shown = money_col(tx, ["amount_pence"]).rename(columns={
        "date": "Date", "account": "Account", "description": "Description", "amount_pence": "Amount",
        "pending": "Pending", "category": "Category", "source": "Set by"})
    # The selection is a row NUMBER, so it must not survive a change to the list it pointed into. Keying the table on
    # the filters and a save counter gives a fresh (empty) selection whenever the rows could have changed.
    table_key = f"txn_table_{month}_{account}_{category}_{text}_{inc_t}_{st.session_state.get('txn_saves', 0)}"
    event = st.dataframe(shown[["Date", "Account", "Description", "Amount", "Pending", "Category", "Set by"]],
                         hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                         key=table_key, column_config=money_cfg("Amount"),
                         alt="Transactions; select a row to recategorise it")
    st.caption(f"{len(tx)} lines shown (max 300). Click a row to recategorise it." if len(tx)
               else "No transactions match these filters.")

    # Step 2 (shown after a save): confirm similar transactions. Takes priority over picking a new row.
    pend = st.session_state.get("similar")
    if pend:
        st.success(f"Saved: {pend['desc'][:60]} → {pend['category']}")
        sim = classify.find_similar(pend["id"], pend["category"])
        if not sim:
            st.session_state.pop("similar")
            st.rerun()
        st.markdown(f"**{len(sim)} similar transaction(s) found.** Tick the ones that should also be "
                    f"**{pend['category']}**. Exact matches are ticked; related ones are not.")
        sdf = pd.DataFrame(sim)
        sdf.insert(0, "Apply", sdf["match"].eq("exact"))
        sdf = money_col(sdf.rename(columns={"amount_pence": "Amount"}), ["Amount"])
        edited = st.data_editor(
            sdf[["Apply", "match", "date", "account", "description", "Amount", "current_category"]],
            hide_index=True, width="stretch", key=f"sim_editor_{pend['id']}",
            disabled=["match", "date", "account", "description", "Amount", "current_category"],
            column_config={"match": "Match", "date": "Date", "account": "Account", "description": "Description",
                           "current_category": "Now", "Apply": st.column_config.CheckboxColumn("Apply"),
                           **money_cfg("Amount")})
        chosen = [int(sim[i]["id"]) for i in edited.index[edited["Apply"]]]
        remember = st.checkbox(f"Also apply to future transactions whose text contains:", value=True, key="sim_rule")
        rule_text = st.text_input("Text to match (not case sensitive)", value=pend["key"].title(),
                                  key=f"sim_pat_{pend['id']}", disabled=not remember)
        b1, b2 = st.columns([1, 1])
        if b1.button(f"Apply to {len(chosen)} selected", type="primary", key="sim_apply"):
            try:
                n = classify.apply_to_many(chosen, pend["category"], rule_text if remember else None)
                st.session_state["txn_saves"] = st.session_state.get("txn_saves", 0) + 1
                st.session_state.pop("similar")
                st.session_state["flash"] = f"Updated {n} transaction(s)" + (" and saved a rule." if remember else ".")
                st.rerun()
            except ValueError as e:
                st.error(str(e))
        if b2.button("Skip - just the one I set", key="sim_skip"):
            st.session_state["txn_saves"] = st.session_state.get("txn_saves", 0) + 1
            st.session_state.pop("similar")
            st.rerun()
        st.caption("Lines you set manually and transfers are never listed. Nothing changes until you press Apply.")
    else:
        if st.session_state.get("flash"):
            st.success(st.session_state.pop("flash"))
        rows = [i for i in (event.selection.rows if event and event.selection else []) if 0 <= i < len(tx)]
        if rows:
            r = tx.iloc[rows[0]]
            st.markdown(f"**Selected:** {r['date']} · {r['description'][:70]} · {fmt_money(r['amount_pence'])}")
            known = [c for c in data.category_names() if c != "Uncategorised"]
            c1, c2 = st.columns(2)
            pick = c1.selectbox("Category", known + ["+ New category…"], key="rc_pick")
            new = c2.text_input("New category name", key="rc_new") if pick == "+ New category…" else ""
            if st.button("Save category", type="primary", key="rc_apply"):
                try:
                    chosen_cat = (new if pick == "+ New category…" else pick).strip()
                    classify.recategorise(int(r["id"]), chosen_cat)
                    st.session_state["txn_saves"] = st.session_state.get("txn_saves", 0) + 1
                    st.session_state["similar"] = {"id": int(r["id"]), "category": chosen_cat,
                                                   "desc": r["description"], "key": classify.merchant_key(r["description"])}
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

# ---------- auto-categorise ----------
with tab_auto:
    from ft import autoclassify as ac
    from ft import localdict as ld
    st.subheader("Local dictionary (free, nothing leaves your machine)")
    lp = ld.plan()
    l1, l2, l3 = st.columns(3)
    l1.metric("Known merchants", len(lp["matched"]), border=True, height="stretch")
    l2.metric("Lines covered", sum(m["lines"] for m in lp["matched"]), border=True, height="stretch")
    l3.metric("Left for you", len(lp["unmatched"]) + len(lp["ambiguous"]), border=True, height="stretch")
    with st.expander("What the dictionary would apply"):
        st.dataframe(pd.DataFrame(lp["matched"]).rename(columns={"merchant": "Merchant", "category": "Category",
                     "confidence": "Confidence", "lines": "Lines", "keyword": "Matched on"}) if lp["matched"] else
                     pd.DataFrame({"Merchant": []}), hide_index=True, width="stretch", alt="Merchants the local dictionary would categorise")
    if st.button("Apply local dictionary", type="primary", disabled=not lp["matched"], key="local_go"):
        res = ld.apply()
        st.session_state["flash_auto"] = (f"Categorised {res['applied']} transactions across {res['merchants']} merchants as "
                                          "suggestions. Review them below.")
        st.rerun()
    st.caption("Add merchants by editing RULES in src/ft/localdict.py. Applied results are suggestions you can change below.")
    if st.session_state.get("flash_auto"):
        st.success(st.session_state.pop("flash_auto"))

    with st.expander("Optional: ask an AI model for the rest (sends merchant labels over the internet)"):
        st.caption("Only short merchant labels are sent, never amounts, dates, accounts or full descriptions. Anything that "
                   "looks like a payment to a person is held back. Suggestions never override your own choices, rules or transfers.")
        if ac.backend() == "gateway":
            st.warning("Labels go through the company gateway, which is logged by its operators and uses a work credential. "
                       "Check that this is allowed before sending.", title="Gateway backend")
        else:
            st.caption("Backend: personal Anthropic API key (FT_ANTHROPIC_API_KEY).")
        cand = ac.candidates()
        c1, c2 = st.columns(2)
        c1.metric("Merchants to classify", len(cand["send"]), border=True, height="stretch")
        c2.metric("Held back (look personal)", len(cand["withheld"]), border=True, height="stretch")
        st.markdown(f"**Preview: exactly what would be sent ({len(cand['send'])} labels)**")
        st.dataframe(pd.DataFrame({"Label sent": [x["label"] for x in cand["send"]]}), hide_index=True, width="stretch",
                     height=220, alt="Merchant labels that would be sent")
        if cand["withheld"]:
            st.markdown(f"**Held back, categorise by hand ({len(cand['withheld'])})**")
            st.dataframe(pd.DataFrame(cand["withheld"]).rename(columns={"label": "Label", "reason": "Why", "count": "Lines"}),
                         hide_index=True, width="stretch", alt="Merchant labels withheld from the request")
        ok = st.checkbox("I have read the preview and approve sending these labels", key="auto_ok")
        if st.button("Suggest categories", disabled=not (ok and cand["send"]), key="auto_go"):
            try:
                with st.spinner("Asking for suggestions..."):
                    res = ac.suggest()
                st.session_state["flash_auto"] = (f"Sent {res['sent']} labels, stored {res['stored']} suggestions, "
                                                  f"applied to {res['applied']} transactions.")
                st.rerun()
            except SystemExit as e:
                st.error(str(e))
            except Exception as e:  # network, auth, rate limit: show it, change nothing
                st.error(f"{type(e).__name__}: {e}")

    st.subheader("Review suggestions")
    rev = data.auto_review()
    if rev.empty:
        st.info("No suggested categories waiting for review.")
    else:
        cats = sorted(set(ac.DEFAULT_CATEGORIES) | set(data.category_names()) - {"Uncategorised", "Transfer"})
        view = rev[["merchant", "category", "lines", "spend_pence", "confidence"]].copy()
        view.insert(0, "Confirm", False)
        view = money_col(view.rename(columns={"spend_pence": "Spend"}), ["Spend"])
        edited = st.data_editor(
            view[["Confirm", "merchant", "category", "lines", "Spend", "confidence"]], hide_index=True, width="stretch",
            key="auto_editor", alt="Suggested categories by merchant; change the category or tick Confirm",
            disabled=["merchant", "lines", "Spend", "confidence"],
            column_config={"Confirm": st.column_config.CheckboxColumn("Confirm", help="Marks the suggestion as yours"),
                           "merchant": "Merchant", "lines": "Lines", "confidence": "Confidence",
                           "category": st.column_config.SelectboxColumn("Category", options=cats, required=True),
                           **money_cfg("Spend")})
        changed = [i for i in edited.index if edited.at[i, "category"] != rev.at[i, "category"]]
        confirmed = [i for i in edited.index if bool(edited.at[i, "Confirm"])]
        st.caption("Change a category to override it, or tick Confirm to keep it. Both mark the lines as yours, so a future "
                   "suggestion can't change them. Untouched rows stay as suggestions.")
        if st.button(f"Save {len(set(changed) | set(confirmed))} decision(s)", disabled=not (changed or confirmed), key="auto_save"):
            try:
                n = 0
                for i in sorted(set(changed) | set(confirmed)):
                    n += classify.apply_to_many(rev.at[i, "ids"], edited.at[i, "category"],
                                                rule_pattern=rev.at[i, "merchant"] if len(rev.at[i, "merchant"]) >= 3 else None)
                st.session_state["flash_auto"] = f"Saved {n} transaction(s) and remembered the merchants as rules."
                st.rerun()
            except ValueError as e:
                st.error(str(e))

# ---------- holdings ----------
with tab_hold:
    raw_h = data.holdings()
    if raw_h.empty:
        st.info("No holdings yet. Run the Trading 212 sync.")
    else:
        # One account at a time: totals, cash, the pie and diversification are all per account, never summed across accounts.
        _hacc = raw_h[["account_id", "account"]].drop_duplicates().sort_values("account")
        if len(_hacc) > 1:
            _pick = st.selectbox("Account", _hacc["account_id"].tolist(), key="hold_account",
                                 format_func=lambda i: _hacc.loc[_hacc["account_id"] == i, "account"].iloc[0])
            raw_h = raw_h[raw_h["account_id"] == _pick]
        v = data.value_holdings(raw_h)
        acc_total =float(v["account_total_pence"].iloc[0]) / 100 if pd.notna(v["account_total_pence"].iloc[0]) else None
        invested = float(v["value_gbp"].sum(skipna=True))
        cash = (acc_total - invested) if acc_total is not None else 0.0
        m1, m2, m3 = st.columns(3)
        m1.metric("ISA total", fmt_money(round(acc_total * 100)) if acc_total is not None else "–", border=True, height="stretch")
        m2.metric("In holdings", fmt_money(round(invested * 100)), border=True, height="stretch")
        m3.metric("Cash / unexplained", fmt_money(round(cash * 100)), border=True, height="stretch",
                  help="Total minus the value of the positions. Normally near zero for an invested ISA.")

        order = v.sort_values("value_gbp", ascending=False)
        right, left = st.container(), st.container()      # pie first, full-width table underneath
        with right:
            st.subheader("Where the money is")
            pie = order[["ticker", "name", "value_gbp"]].copy()
            pie["label"] = pie["name"].fillna(pie["ticker"])
            top = pie.head(9)
            rest = pie.iloc[9:]["value_gbp"].sum()
            slices = top[["label", "value_gbp"]].copy()
            if rest > 0.005:
                slices.loc[len(slices)] = ["Other holdings", rest]
            if cash > 0.5:
                slices.loc[len(slices)] = ["Cash", cash]
            # Ten clearly different colours (brand blues/teal/orange/pink plus neutrals). Labels show each slice's share, so the
            # chart can be read without matching legend swatches.
            palette = ["#1428A0", "#00C3B2", "#FF7F3F", "#E33B9A", "#0077C8", "#75787B", "#069BAE", "#9B59B6", "#F2C230", "#8FD14F", "#000000"]
            slices["share"] = slices["value_gbp"] / slices["value_gbp"].sum()
            slices["pct_label"] = slices["share"].map(lambda x: "" if HIDE or x < 0.04 else f"{x:.0%}")
            base = alt.Chart(slices).encode(
                theta=alt.Theta("value_gbp:Q", stack=True),
                color=alt.Color("label:N", title=None, sort=list(slices["label"]),
                                scale=alt.Scale(domain=list(slices["label"]), range=palette[:len(slices)]),
                                legend=alt.Legend(labelLimit=260, symbolType="square", symbolSize=140, labelFontSize=12)),
                order=alt.Order("value_gbp:Q", sort="descending"))
            arcs = base.mark_arc(innerRadius=55, outerRadius=130, stroke="#F3F3F3", strokeWidth=2).encode(
                tooltip=[] if HIDE else [alt.Tooltip("label:N", title="Holding"), alt.Tooltip("value_gbp:Q", title="Value (GBP)", format=",.2f")])
            labels = alt.Chart(slices).mark_text(radius=95, size=14, fontWeight="bold", color="white").encode(
                theta=alt.Theta("value_gbp:Q", stack=True), text=alt.Text("pct_label:N"),
                order=alt.Order("value_gbp:Q", sort="descending"))
            chart = (arcs + labels).properties(height=360)
            st.altair_chart(chart, width="stretch")
            st.caption("Slices add up to the ISA total" + (" (the cash slice is the part not held in positions)." if cash > 0.5 else "."))
        with left:
            st.subheader("Holdings by value")
            t = order.rename(columns={"ticker": "Ticker", "name": "Name", "quantity": "Quantity", "avg_price": "Avg price",
                                      "current_price": "Price", "currency": "Ccy", "value_gbp": "Current value",
                                      "pct_of_positions": "% of holdings"}).copy()
            t["Current value"] = t["Current value"] * 100                       # money_col expects pence
            t = money_col(t, ["Current value"])
            t["Value basis"] = t["est"].map({True: "estimated (FX)", False: "from price"})
            if HIDE:
                for col in ("Quantity", "Avg price", "Price"):
                    t[col] = t[col].map(lambda x: fmt_num(x, 4 if col == "Quantity" else 2))
                t["% of holdings"] = "xx.x%"
            cols = ["Name", "Ticker", "Current value", "% of holdings", "Quantity", "Price", "Ccy", "Value basis"]
            st.dataframe(t[cols], hide_index=True, width="stretch", alt="ISA holdings sorted by current value",
                         column_config=({} if HIDE else {
                             **money_cfg("Current value"),
                             "% of holdings": st.column_config.NumberColumn("% of holdings", format="%.1f%%"),
                             "Quantity": st.column_config.NumberColumn("Quantity", format="%.4f"),
                             "Price": st.column_config.NumberColumn("Price", format="%,.2f")}))
        dv = resilience.diversification(v)
        if dv["ok"]:
            st.divider()
            st.subheader("Diversification")
            d1, d2, d3, d4 = st.columns(4)
            d1.metric("Funds held", dv["n"], border=True, height="stretch")
            d2.metric("Largest holding", "xx.x%" if HIDE else f"{dv['top1']:.1f}%", border=True, height="stretch",
                      help=dv["top1_name"] if not HIDE else None)
            d3.metric("Top 3 holdings", "xx.x%" if HIDE else f"{dv['top3']:.1f}%", border=True, height="stretch")
            d4.metric("US equity funds", dv["us_eq_funds"], border=True, height="stretch",
                      help="Several S&P 500 / MSCI USA funds hold largely the same companies, so they overlap.")
            ga, gb = st.columns(2)
            with ga:
                st.markdown("**By asset class**")
                bc = dv["by_class"].rename_axis("class").reset_index(name="pct")
                st.altair_chart(bar_chart(bc, "class", "pct", horizontal=True, height=200), width="stretch")
            with gb:
                st.markdown("**Equities by region**")
                br = dv["by_region_eq"].rename_axis("region").reset_index(name="pct")
                st.altair_chart(bar_chart(br, "region", "pct", horizontal=True, height=200), width="stretch")
            if not HIDE:
                notes = []
                if dv["us_eq_pct_of_isa"] >= 25:
                    notes.append(f"{dv['us_eq_funds']} US equity funds make up {dv['us_eq_pct_of_isa']:.0f}% of the ISA, so a US "
                                 "downturn would hit a large share. Consider whether that is intended.")
                if dv["top1"] >= 15:
                    notes.append(f"Largest single fund is {dv['top1']:.0f}% of the ISA.")
                bonds = float(dv["by_class"].get("Bonds", 0))
                if bonds < 10:
                    notes.append(f"Bonds are only {bonds:.0f}% of the ISA, so there is little cushion against an equity fall.")
                for n_ in notes:
                    st.info(n_)
            st.caption("Asset class and region are inferred from each fund's name, not from what it actually holds. 'Global' "
                       "funds are mostly US in practice, so US exposure is likely understated. Axis values are percent of the ISA.")
        st.caption("Current value is in pounds. GBX prices are pence, so they are divided by 100. Holdings priced in another "
                   "currency use an exchange rate worked out from the account total, so those rows are estimates; the "
                   "Trading 212 total is exact.")

# ---------- manual accounts & ownership ----------
with tab_manual:
    st.subheader("Add a manual account")
    st.caption("For savings the bank API doesn't expose, pensions, and the mortgage.")
    with st.form("add_account", clear_on_submit=True):
        a1, a2, a3, a4 = st.columns([1, 2, 1.4, 1])
        kind = a1.selectbox("Type", list(manual.KINDS))
        name = a2.text_input("Name", placeholder="e.g. Example Bank Monthly Saver")
        provider = a3.text_input("Provider (bank)", placeholder="e.g. Santander")
        share = a4.number_input("Your share %", 1, 100, 100)
        b1, b2 = st.columns(2)
        opening = b1.number_input("Opening balance (£, optional)", min_value=0.0, step=100.0, format="%.2f")
        opening_date = b2.date_input("Balance date", value=date.today())
        if st.form_submit_button("Add account"):
            try:
                manual.add_account(kind, name, int(share), provider)
                if opening > 0:
                    manual.save_balance(name.strip(), round(opening, 2), opening_date.isoformat())
                st.success(f"Added '{name.strip()}'.")
                st.rerun()
            except ValueError as e:
                st.error(str(e))

    st.subheader("Update a balance")
    man = data.manual_accounts()
    if man.empty:
        st.info("No manual accounts yet. Add one above.")
    else:
        st.caption("Enter mortgages and cards as positive amounts - they are stored as money you owe.")
        with st.form("set_balance", clear_on_submit=True):
            u1, u2, u3 = st.columns([2, 1, 1])
            which = u1.selectbox("Account", man["name"].tolist())
            amt = u2.number_input("Balance (£)", min_value=0.0, step=100.0, format="%.2f")
            when = u3.date_input("As of", value=date.today())
            if st.form_submit_button("Save balance"):
                try:
                    pence, _ = manual.save_balance(which, round(amt, 2), when.isoformat())
                    st.success(f"Saved {which}: {fmt_money(pence)} as of {when.isoformat()}.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
        view = money_col(man.rename(columns={"name": "Account", "provider": "Provider", "kind": "Type",
                                             "share_pct": "Share %", "as_of": "As of", "balance_pence": "Balance"}), ["Balance"])
        st.dataframe(view[["Provider", "Account", "Type", "Balance", "Share %", "As of"]], hide_index=True, width="stretch",
                     column_config=money_cfg("Balance"), alt="Manual accounts with their latest balance")
        with st.expander("Balance history"):
            pick = st.selectbox("Account", man["name"].tolist(), key="hist_pick")
            hist = data.balance_history(int(man.loc[man["name"] == pick, "id"].iloc[0]))
            st.dataframe(money_col(hist, ["balance_pence"]).rename(columns={"as_of": "As of", "balance_pence": "Balance"}),
                         hide_index=True, width="stretch", column_config=money_cfg("Balance"),
                         alt="Balance history for the selected manual account")

    st.subheader("Property equity")
    st.caption("Link your house value and mortgage to show equity. The price index is shown as context only "
               "and never changes your valuation. Update the house value in the form above whenever you get a new valuation.")
    houses = man[man["kind"].isin(["other", "property"])] if not man.empty else man
    mortgages = man[man["kind"] == "mortgage"] if not man.empty else man
    if houses.empty:
        st.info("Add a manual account for the house first (type 'other').")
    else:
        cur = prop.get_settings()
        with st.form("property_form"):
            p1, p2 = st.columns(2)
            # Default to what is already saved. Without this the form would start on the first option, and saving it
            # untouched would silently unlink the mortgage.
            house_names = houses["name"].tolist()
            saved_house = houses.loc[houses["id"] == cur["account_id"], "name"].tolist() if cur else []
            house_name = p1.selectbox("House account", house_names, key="prop_house",
                                      index=house_names.index(saved_house[0]) if saved_house else 0)
            mort_opts = ["(none)"] + mortgages["name"].tolist()
            saved_mort = mortgages.loc[mortgages["id"] == cur["mortgage_account_id"], "name"].tolist() if cur else []
            mort_name = p2.selectbox("Mortgage account", mort_opts, key="prop_mort",
                                     index=mort_opts.index(saved_mort[0]) if saved_mort else 0)
            p3, p4 = st.columns(2)
            region = p3.text_input("Price index area", value=cur["hpi_region"].replace("-", " ").title() if cur else "",
                                   help="A local authority name as used by the UK House Price Index, e.g. Leeds or Bristol.")
            types = list(prop.TYPE_FIELDS)
            ptype = p4.selectbox("Property type", types, index=types.index(cur["property_type"]) if cur else 0)
            if st.form_submit_button("Save property settings"):
                try:
                    hid = int(houses.loc[houses["name"] == house_name, "id"].iloc[0])
                    mid = None if mort_name == "(none)" else int(mortgages.loc[mortgages["name"] == mort_name, "id"].iloc[0])
                    prop.save_settings(hid, mid, region, ptype)
                    hpi_context.clear()
                    st.success("Saved.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

    st.subheader("Provider (bank) for any account")
    st.caption("Bank-connected accounts get their provider from the bank automatically. Set it here for manual accounts, "
               "or to correct one. Leave it blank to clear it.")
    _all = data.accounts_latest()
    if _all.empty:
        st.info("No accounts yet. Add one above, or connect a bank or Trading 212 from the sidebar.")
    else:
        with st.form("provider_form"):
            v1, v2 = st.columns([3, 2])
            who = v1.selectbox("Account", _all["name"].tolist(), key="prov_acc",
                               format_func=lambda n: f"{n}  ·  {_all.loc[_all['name'] == n, 'provider'].iloc[0] or 'no provider'}")
            known = sorted({p for p in _all["provider"] if p})
            new_p = v2.text_input("Provider", value=_all.loc[_all["name"] == who, "provider"].iloc[0], key="prov_val",
                                  help="Existing providers: " + (", ".join(known) if known else "none yet"))
            if st.form_submit_button("Save provider"):
                try:
                    manual.set_provider(int(_all.loc[_all["name"] == who, "id"].iloc[0]), new_p)
                    st.success(f"{who}: {new_p.strip() or 'cleared'}")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

    st.subheader("Ownership share (any account)")
    st.caption("Joint accounts: set 50 so net worth counts only your half.")
    allacc = data.accounts_latest()
    if allacc.empty:
        st.info("No accounts yet.")
    else:
        with st.form("share_form"):
            s1, s2 = st.columns([3, 1])
            target = s1.selectbox("Account", allacc["name"].tolist(), key="share_acc")
            cur = int(allacc.loc[allacc["name"] == target, "share_pct"].iloc[0])
            pct = s2.number_input("Your share %", 1, 100, cur)
            if st.form_submit_button("Save share"):
                try:
                    manual.set_share(int(allacc.loc[allacc["name"] == target, "id"].iloc[0]), int(pct))
                    st.success(f"{target}: {int(pct)}%")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
