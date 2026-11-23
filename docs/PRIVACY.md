# Privacy and safety

MoneyPT is built so that your financial data stays on **your computer**. This page says exactly what is stored, what
leaves your machine, and what you are responsible for. It describes what the code in this repository does. It is not a
legal or security guarantee.

## What is stored, and where

| What | Where | Protection |
|---|---|---|
| Accounts, balances, transactions, categories, settings | `finance.db` (SQLite file in the project folder, or `FT_DB_PATH`) | **Not encrypted.** Anyone who can read the file can read your data. Use your computer's disk encryption (BitLocker on Windows, FileVault on macOS). |
| Bank sign-in tokens | Inside `finance.db` | Encrypted with the key in `.env` |
| Trading 212 keys added in the dashboard | Inside `finance.db` | Encrypted with the key in `.env` |
| Encryption key, TrueLayer client secret, any keys you type into `.env` | `.env` | **Plain text.** Keep it private. |

Your encryption key is the only thing protecting stored tokens. If someone has both `finance.db` and `.env`, they can
use your stored tokens until you withdraw consent (see below).

## What leaves your computer

Only the connections you choose to set up. Nothing is sent anywhere until you do.

| To | When | What is sent |
|---|---|---|
| **TrueLayer** (`auth.truelayer.com`, `api.truelayer.com`) | Only if you connect a bank | Your app credentials and bank tokens. It returns your accounts, balances and transactions. |
| **Trading 212** (`live.trading212.com`) | Only if you add a key | Your read-only key. It returns balances and holdings. |
| **UK Land Registry** (`landregistry.data.gov.uk`) | If you set up a property on the Overview tab | A region name and property type only. No personal data. |
| **Anthropic** (`api.anthropic.com`), or a gateway you configure | Only if you use **Suggest categories** and set a key | Cleaned merchant names only, after a preview you approve. Never amounts, dates or account details. Names you list in `FT_OWN_NAMES` are never sent. |

The default categorisation (**Apply local dictionary**) runs entirely on your computer and sends nothing.

Streamlit's usage statistics are switched off. `python run.py` starts the dashboard so that it listens only on
`127.0.0.1`, which means other devices on your network cannot open it. If Streamlit is started some other way and is open
to your network, MoneyPT refuses to show any data and tells you to use `python run.py`. This was tested; it is still
worth keeping your computer's firewall on.

## Bank access is read-only

The app asks TrueLayer for these permissions: `info accounts balance cards transactions offline_access`. There is no
payment permission, so the app cannot move money. It can read balances and transactions.

## Consent expires

Under UK rules, access to your bank data must be reconfirmed about **every 180 days**. The app warns you in the sidebar
when a connection has less than 30 days left. Reconnect it from **Connect a bank account** before it lapses.

You can withdraw consent at any time in your bank's app or website, or by contacting TrueLayer. Doing this stops the
stored tokens working.

## Joint accounts and other people's data

- **Someone else's accounts** (a partner, a family member) need **their own login and consent**. You cannot approve for
  them, and you should only store their data with their agreement.
- A **joint account** may appear once or twice depending on how your bank identifies it. After connecting a second
  person's login, check that no account is listed twice, and back up first.
- Anything you store about other people is your responsibility. Look after the database accordingly.

## Using it at work

Do not point this at personal finance data through a **company LLM gateway, proxy or laptop** unless your employer
explicitly allows it. The optional AI categoriser ignores any gateway configured in your environment and only uses one if
you set it up deliberately in `.env`.

## Before you share or publish anything

- Never commit or send `finance.db`, any `.bak` backup, or `.env`. They are in `.gitignore`.
- Remove screenshots that show real names, balances or transactions. Demo mode hides amounts, not descriptions or account names.
- If you ever commit a secret by mistake, deleting the file is not enough: the old version stays in history. **Rotate it**
  (create a new TrueLayer secret, new Trading 212 keys, a new encryption key) and re-set up your connections.

## Your responsibilities

This is a hobby project provided as is. It is not financial advice, has not been audited, and is not regulated. You
choose what to connect, and you are responsible for your data, your keys and your consent.
