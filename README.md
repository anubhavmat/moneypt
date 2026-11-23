# MoneyPT

A personal money dashboard that runs **only on your own computer**. It pulls your UK bank accounts, cards and
Trading 212 into one view, lets you add things that have no live feed (pensions, mortgage, house value), and helps you
see where your money goes.

- **One net worth number** across current accounts, savings, cards, ISA, pensions, mortgage and property
- **Spending by day, week, month, quarter or year**, by category, with a "month" that starts on the day you are paid
- **Fixed vs flexible spend** and a **watch-list** with monthly limits for habits you want to control
- **Months of cover, savings rate, net worth over time** and a **remortgage readiness** panel
- **Trading 212 holdings** with a value pie chart and a diversification check
- **Categorising that learns**: a free local dictionary, your own rules, and "similar transactions" prompts
- **Demo mode**: amounts are hidden every time the app opens, so you can screen-share safely

> **Single user, single machine.** There are no accounts, logins or servers. Your data lives in one file on your computer.
> This is a hobby project, not financial advice, and not a regulated product. Numbers are only as good as the data you
> connect. Read [Privacy and safety](docs/PRIVACY.md) before connecting anything.

## What you need

| Need | Notes |
|---|---|
| Windows, macOS or Linux | Developed and tested on Windows 11 |
| Python 3.12 or newer | Check with `python --version` |
| About 500 MB free | Mostly Python packages |
| *(Optional)* A TrueLayer developer account | Needed only for live bank connections. Free to create. See [Connecting banks](docs/CONNECTING.md) |
| *(Optional)* Trading 212 API keys | Read-only keys, made in the Trading 212 app |

You can use MoneyPT with **manual accounts only** and skip bank connections entirely.

## Quick start

These commands are for Windows PowerShell. On macOS or Linux use `source venv/bin/activate` and `python3`.

```powershell
# 1. Get the code
git clone https://github.com/anubhavmat/moneypt.git
cd moneypt

# 2. Create an isolated Python environment and install the packages
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# 3. Create your settings file
copy .env.example .env

# 4. Generate your private encryption key (run from the src folder)
cd src
python -m ft.cli genkey
```

Copy the long text it prints. Open `.env` in a text editor and paste it after `FT_ENCRYPTION_KEY=`. Save the file.
**Keep a safe copy of that key.** If you lose it, any bank connections you stored cannot be read and must be set up again.

```powershell
# 5. Go back to the project folder and start the dashboard
cd ..
python run.py
```

Your browser opens at `http://localhost:8501`. The first time, the database is created for you.

Next time, open a terminal in the project folder and run:

```powershell
venv\Scripts\activate
python run.py
```

**Always start it with `python run.py`.** It makes sure the dashboard can only be opened from your own computer. If you
start Streamlit some other way and the server is open to your network, MoneyPT refuses to show anything and tells you why.

### Check it worked

You should see eight tabs and the message "No transactions yet". Open **Manual accounts & ownership**, add an account with
an opening balance, and your **Overview** tab will show it as your net worth.

The dashboard always opens with **amounts hidden** (`£x,xxx.xx`). Switch off **Demo mode: hide amounts** in the sidebar to
see real figures.

## Next steps

1. **Add what has no live feed**: pensions, mortgage, house value, savings. See [Adding accounts](docs/ADDING-ACCOUNTS.md).
2. **Connect banks** (optional): [Connecting banks](docs/CONNECTING.md), including what to know about consent, expiry and
   joint accounts.
3. **Teach it your transfers**: set `FT_TRANSFER_PATTERNS` in `.env` so moves between your own accounts are not counted as spending.
4. **Categorise**: click *Apply local dictionary* on the **Auto-categorise** tab, then fix the rest by hand. It learns from you.

## Where your data is

| What | Where |
|---|---|
| Your transactions, balances, settings | `finance.db` in the project folder (or the path in `FT_DB_PATH`) |
| Your secrets | `.env` in the project folder |
| Bank sign-in tokens | Inside `finance.db`, **encrypted** with the key in `.env` |

Both files are listed in `.gitignore`, so Git will not upload them. **Never share, email or commit `finance.db` or `.env`.**
To back up, copy `finance.db` somewhere private.

## Getting help

- Setup problems: [Troubleshooting](docs/TROUBLESHOOTING.md)
- Questions about what is stored and sent: [Privacy and safety](docs/PRIVACY.md)

## Licence

See `LICENSE`. This project is provided as is, with no warranty.
