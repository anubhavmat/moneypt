# Connecting banks (TrueLayer)

MoneyPT reads your UK bank accounts and cards through **TrueLayer**, a regulated open banking provider. You create your
**own** free developer application, so the connection is yours and nobody else's credentials are involved.

This is optional. Manual accounts work without any of this.

> TrueLayer's website and sign-up steps change from time to time, and this guide was written without access to their
> console. Treat the TrueLayer parts as a map, and follow [TrueLayer's own documentation](https://docs.truelayer.com/)
> where it differs.

## Before you start

- **Read [Privacy and safety](PRIVACY.md).** You are giving an app read access to your bank data.
- **Consent lasts about 180 days**, then each bank asks you to approve again. The app warns you 30 days before.
- **Each person approves their own accounts.** Do not connect someone else's accounts without them.
- **Access is read-only.** The app requests: `info accounts balance cards transactions offline_access`. It cannot make payments.

## Step 1: Create your TrueLayer application

1. Create a free account at TrueLayer's developer console (start from [docs.truelayer.com](https://docs.truelayer.com/)).
2. Create an application and note its **Client ID** and **Client Secret**.
3. Add this **redirect URI** to the application, exactly as shown (it must match `.env` character for character):

   ```
   http://localhost:3000/callback
   ```
4. Start in the **sandbox** (test) environment to check everything works, then switch to **live** when you are ready to use
   real banks. Live access for personal use may need to be enabled on your account in TrueLayer's console.

## Step 2: Put the details in `.env`

Open your `.env` file and fill in:

```
TL_ENV=sandbox
TL_CLIENT_ID=your-client-id
TL_CLIENT_SECRET=your-client-secret
TL_REDIRECT_URI=http://localhost:3000/callback
```

Use `TL_ENV=live` later for real banks. Sandbox and live connections are kept separate, and the app only syncs the one
that matches `TL_ENV`.

**Restart the dashboard** after editing `.env`. It only reads the file when it starts.

## Step 3: Connect a bank from the dashboard

1. Switch **Demo mode** off in the sidebar. Connecting is disabled while amounts are hidden.
2. Open **Connect a bank account** in the sidebar.
3. Choose how the account holder will approve:
   - **On this computer**: the app waits for the bank to send you back. Use this for banks where you log in on the website.
   - **In the bank's phone app**: for banks that make you approve in their mobile app.
4. Click **Start connecting**, then **Open the bank's consent page**.
5. **Whoever owns the accounts logs in at their bank and approves.** Choose every account and card you want included.

**If you chose "On this computer":** after you approve, return to MoneyPT. It finishes by itself and shows **Bank connected**.

**If you chose the phone-app route:** after you approve in the app, your browser tries to open a page that fails to load.
That is expected. Copy that page's **full address**, paste it into the box in MoneyPT, and click **Finish connecting**.

6. Click **Sync all accounts** in the sidebar. Accounts and transactions appear on the Overview and Transactions tabs.

You can also connect from a terminal: from the `src` folder run `python -m ft.cli connect`, or add `paste` for the
phone-app route.

## Step 4: Keep it working

- **Sync whenever you like** with **Sync all accounts**. It never creates duplicates.
- **Reconnect every ~180 days.** When the sidebar warns that consent is ending, repeat Step 3 for that bank.
- **Withdraw access** any time in your bank's app or website. The stored token then stops working.

## Joint accounts and partners

- A **joint account** is the same account under two logins. Connecting both logins may add it once or twice, depending
  on how your bank identifies it.
- **Back up first** (copy `finance.db`), connect the second person, then check that no account appears twice on the Overview tab.
- If an account is duplicated, restore your backup, or ask in an issue on the repository.
- **Spending is pooled.** Everything you connect counts together in Spending, Habits and the cover figures. There is no
  personal-only filter yet.
- **Card repayments between your own accounts** (for example from a joint current account to a partner's card) are paired
  automatically as transfers when both sides are connected, so they are not counted twice. If you categorised the
  payment by hand before connecting the other side, change it to **Transfer**.

## Common problems

See [Troubleshooting](TROUBLESHOOTING.md).
