"""Start MoneyPT. Run from the project folder:  python run.py

Always binds to this computer only (127.0.0.1), whichever folder you start from, so nobody else on your network can open
the dashboard. Extra arguments are passed to Streamlit, e.g.  python run.py --server.port 8502
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

cmd = [sys.executable, "-m", "streamlit", "run", str(ROOT / "src" / "app.py"),
       "--server.address=127.0.0.1",          # local only. Do not change this unless you understand the risk.
       "--browser.gatherUsageStats=false"] + sys.argv[1:]
raise SystemExit(subprocess.call(cmd, cwd=str(ROOT)))
