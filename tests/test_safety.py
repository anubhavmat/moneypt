"""Checks that protect the people who run MoneyPT. Run from the project folder:  python -m pytest tests   (pip install pytest)

They use a throwaway database and never touch real data, a real bank or the network.
"""
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture()
def clean_env(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("TL_", "FT_", "T212_", "ANTHROPIC")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FT_DB_PATH", str(tmp_path / "test.db"))
    return tmp_path


def test_database_defaults_to_project_folder_not_cwd(monkeypatch, tmp_path):
    from ft import db
    monkeypatch.delenv("FT_DB_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    assert Path(db.db_path()).parent == ROOT


def test_blank_db_path_also_uses_project_folder(monkeypatch):
    from ft import db
    monkeypatch.setenv("FT_DB_PATH", "   ")
    assert Path(db.db_path()).parent == ROOT


def test_app_starts_on_an_empty_database_without_errors(clean_env):
    from streamlit import config
    from streamlit.testing.v1 import AppTest
    config.set_option("server.address", "127.0.0.1")
    at = AppTest.from_file(str(ROOT / "src" / "app.py"), default_timeout=120).run()
    assert not at.exception
    assert len(at.tabs) == 8


@pytest.mark.parametrize("address", [None, "", "0.0.0.0", "192.168.0.8"])
def test_app_refuses_to_show_data_when_open_to_the_network(clean_env, address):
    from streamlit import config
    from streamlit.testing.v1 import AppTest
    config.set_option("server.address", address)
    at = AppTest.from_file(str(ROOT / "src" / "app.py"), default_timeout=120).run()
    assert len(at.tabs) == 0 and at.error


def test_demo_mode_is_on_by_default(clean_env):
    from streamlit import config
    from streamlit.testing.v1 import AppTest
    config.set_option("server.address", "127.0.0.1")
    at = AppTest.from_file(str(ROOT / "src" / "app.py"), default_timeout=120).run()
    assert at.session_state["hide_amounts"] is True


def test_trading212_keys_are_stored_encrypted(clean_env, monkeypatch):
    import base64

    import httpx
    from cryptography.fernet import Fernet
    monkeypatch.setenv("FT_ENCRYPTION_KEY", Fernet.generate_key().decode())
    from ft import db, trading212
    db.init()
    ok = httpx.MockTransport(lambda r: httpx.Response(200, json={"id": 1, "currency": "GBP", "totalValue": 1.0}))
    assert trading212.add_key("Test", "isa", "PLAINKEY123", "PLAINSECRET456", ok)["ok"]
    raw = Path(os.environ["FT_DB_PATH"]).read_bytes()
    assert b"PLAINKEY123" not in raw and b"PLAINSECRET456" not in raw
    assert trading212.list_stored_keys()[0]["key_tail"] == "Y123"


def test_trading212_rejects_a_bad_key_and_stores_nothing(clean_env, monkeypatch):
    import httpx
    from cryptography.fernet import Fernet
    monkeypatch.setenv("FT_ENCRYPTION_KEY", Fernet.generate_key().decode())
    from ft import db, trading212
    db.init()
    bad = httpx.MockTransport(lambda r: httpx.Response(401, json={}))
    assert not trading212.add_key("Test", "isa", "K", "S", bad)["ok"]
    assert trading212.list_stored_keys() == []


def test_bank_connection_rejects_a_mismatched_state(clean_env, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("FT_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("TL_CLIENT_ID", "x")
    monkeypatch.setenv("TL_CLIENT_SECRET", "y")
    monkeypatch.setenv("TL_REDIRECT_URI", "http://localhost:3000/callback")
    from ft import db, truelayer
    db.init()
    a = truelayer.begin_connect(listen=False)
    r = truelayer.submit_pasted(a["state"], "http://localhost:3000/callback?code=abc&state=SOMETHING_ELSE")
    assert r["status"] == "error"
    truelayer.cancel_pending()


def test_no_personal_data_or_secrets_in_the_repository():
    """Fails if a database, backup or .env is present, or if a secret-shaped value is in a tracked file."""
    banned_files = [p for p in ROOT.rglob("*") if p.is_file() and "venv" not in p.parts and
                    (p.suffix in (".db", ".bak", ".sqlite", ".pem", ".key") or p.name == ".env")]
    assert not banned_files, f"private files present: {[str(p) for p in banned_files]}"
    for p in list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.md")) + [ROOT / ".env.example"]:
        if "venv" in p.parts or "tests" in p.parts:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            m = re.match(r"\s*(FT_ENCRYPTION_KEY|TL_CLIENT_SECRET|TL_CLIENT_ID|T212_\w*(KEY|SECRET))\s*=\s*(\S+)", line)
            if m and not re.match(r"(your[-_]|<)", m.group(3), re.I):      # an obvious placeholder is fine
                raise AssertionError(f"{p.name} contains a value for {m.group(1)}")
