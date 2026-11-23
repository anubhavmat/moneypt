# Adding accounts

There are three ways to get an account into MoneyPT. Start with manual accounts: they need no setup.

## 1. Manual accounts (no connection needed)

Use these for anything without a live feed: pensions, a mortgage, a house value, savings, or a bank you don't want to connect.

1. Open the **Manual accounts & ownership** tab.
2. Under **Add a manual account**, fill in a name, the type (savings, pension, mortgage, isa, current, card, other),
   the provider (the bank or company), your ownership share and an opening balance.
3. Click **Add account**. It appears on the **Overview** tab and in your net worth.

**Entering a mortgage or a card balance:** type what you owe as a **positive** amount (for example 225000). The app
stores it as a debt and subtracts it from your net worth for you. Do not type a minus sign.

**Keeping it current:** manual balances only change when you update them. Use **Update a balance** on the same tab
whenever you get a statement. A pension or mortgage can be updated monthly or quarterly. Each update is dated, so the
app can show how a balance changed over time.

**Joint accounts:** set your ownership share. For a household view, leave joint accounts at 100%. To see only your
half, set 50.

**Property equity:** add the house as an account of type `other` and the mortgage as type `mortgage`, then link them in
the **Property equity** section on the same tab. Equity is your own valuation minus the mortgage. The app also shows a regional price index as
context. It never changes your valuation.

## 2. Bank accounts and cards (TrueLayer)

Live balances and transactions for UK banks and cards. Needs a free TrueLayer developer account and a few minutes of
setup. See [Connecting banks](CONNECTING.md).

## 3. Trading 212

Live balance and holdings for a Stocks ISA or Invest account.

1. In the Trading 212 app, create a **read-only API key** (Settings, then API). Give it permission to read account data
   only. Never give it trading permission.
2. In the dashboard sidebar, open **Add a Trading 212 account**.
3. Enter a name (for example "My Stocks ISA"), choose ISA or Invest, and paste the key and secret.
4. Click **Test and save**. The key is tested first. If it works, it is stored **encrypted**.
5. Click **Sync all accounts** to load it.

Each Trading 212 login has its own key. Trading 212's API supports Stocks ISA and Invest accounts. It does not cover
Cash ISAs, so enter a Cash ISA as a manual account.

## What cannot be connected automatically

| Account | Why |
|---|---|
| Mortgages | UK open banking covers payment accounts. Banks have not exposed mortgage accounts in practice. Enter the balance manually. |
| Workplace and personal pensions | There is no public feed an app like this can use. A government pensions dashboard is due to launch publicly in 2027 but it is for people to view, not an API for apps. Enter balances manually. |
| PayPal | There is no personal-account API. Export a statement and categorise by hand. |

Provider coverage changes. Check with your bank or provider if you need the latest.
