# Troubleshooting

## Setup

| Problem | Fix |
|---|---|
| `python` is not recognised | Install Python 3.12 or newer from python.org and tick **Add Python to PATH**. On macOS or Linux use `python3`. |
| `pip install` fails | Make sure the virtual environment is active (you should see `(venv)` at the start of your prompt). Run `pip install --upgrade pip` and try again. |
| `ModuleNotFoundError: No module named 'ft'` | Run commands from the **`src`** folder: `cd src` then `python -m ft.cli ...`. |
| `FT_ENCRYPTION_KEY is empty` | Run `python -m ft.cli genkey` from `src`, paste the result after `FT_ENCRYPTION_KEY=` in `.env`, and save. |
| The dashboard does not open | Open `http://localhost:8501` by hand. If port 8501 is busy, run `python run.py --server.port 8502`. |
| "Unsafe start" error | You started Streamlit directly and it is open to your network. Close it and start with `python run.py` from the project folder. |
| I changed `.env` and nothing happened | Stop the dashboard (Ctrl+C in the terminal) and start it again. It only reads `.env` at start-up. |
| I changed the code and nothing happened | Same: restart the dashboard. |
| Where is my data? | `finance.db` in the project folder, or the path in `FT_DB_PATH`. |

## Bank connections

| Problem | Fix |
|---|---|
| "Bank connections are not set up" | `TL_CLIENT_ID`, `TL_CLIENT_SECRET` and `TL_REDIRECT_URI` must all be set in `.env`, then restart. |
| "Could not listen on localhost:3000" | Another program (or an old connect attempt) is using that port. Close it, or use the phone-app route. |
| "The bank's answer did not match this attempt" | You started a second attempt or used an old tab. Start again and finish in the same attempt. |
| "TrueLayer rejected the one-time code (HTTP 400)" | The code is single-use and short-lived. Start again. |
| "Timed out after 5 minutes" | Start again, and approve within 5 minutes. |
| "authorisation expired or revoked - reconnect this bank" | The ~180-day consent ended, or you withdrew it at the bank. Connect that bank again. |
| A bank is missing from the list | Not every bank is supported, and some limit what is shared. Check TrueLayer's provider coverage. |
| No savings, mortgage or pension accounts | Banks often share only current accounts and cards. Add the rest as manual accounts. |
| An account appears twice | Restore your last `finance.db` backup. See "Joint accounts" in [Connecting banks](CONNECTING.md). |
| Card purchases look like income | Report it as an issue with the bank name. The app flips card amounts so purchases are negative. |

## Trading 212

| Problem | Fix |
|---|---|
| "rejected the key or secret (401)" | Copy both the key and the secret again in full, with no spaces. |
| "key lacks permission (403)" | Create a new key with permission to read account data. |
| "rate limiting" | Wait a minute and try again. Trading 212 allows few requests per second. |
| "That Trading 212 account is already connected" | It is already set up, in `.env` or the dashboard. |
| My Cash ISA is not there | Trading 212's API does not cover Cash ISAs. Add it as a manual account. |

## Numbers that look wrong

| Problem | Fix |
|---|---|
| Spending counted twice | A payment to your own card is probably categorised as spending. Set it to **Transfer**, and make sure both accounts are connected. |
| Savings top-ups appear as spending | Put the category (for example "Savings & investing") in **Count as saved, not spent** in the sidebar. |
| Months of cover looks odd | It uses complete months only and shows how many. With under 3 months it is a rough guide. |
| Net worth is missing something | Accounts with no balance yet are left out. Add a balance or sync. |
| A month looks too low | Months marked **partial** are not fully covered by your synced data. |
| Everything is hidden as `£x,xxx.xx` | That is Demo mode. Switch it off in the sidebar. |

## Still stuck

Open an issue on the repository. **Do not paste your `.env`, your database, or screenshots with real balances or names.**
