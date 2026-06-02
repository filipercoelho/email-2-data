"""The zero-dependency .env loader: replaces `export VAR=...`. Real env vars win; secrets never logged."""

import os

from email2data.config import load_dotenv


def test_load_dotenv_sets_missing_keys_and_parses_forms(tmp_path, monkeypatch):
    monkeypatch.delenv("E2D_A", raising=False)
    monkeypatch.delenv("E2D_B", raising=False)
    monkeypatch.delenv("E2D_C", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "\n"
        "E2D_A=plain\n"
        'E2D_B="quoted value"\n'
        "export E2D_C='single'\n"   # leading `export ` and single quotes both stripped
        "NOEQUALS\n",               # ignored
        encoding="utf-8",
    )
    n = load_dotenv(env)
    assert n == 3
    assert os.environ["E2D_A"] == "plain"
    assert os.environ["E2D_B"] == "quoted value"
    assert os.environ["E2D_C"] == "single"


def test_real_env_var_wins_over_dotenv(tmp_path, monkeypatch):
    monkeypatch.setenv("E2D_PRESET", "from-shell")
    (tmp_path / ".env").write_text("E2D_PRESET=from-file\n", encoding="utf-8")
    load_dotenv(tmp_path / ".env")
    assert os.environ["E2D_PRESET"] == "from-shell"   # already-set value is not clobbered


def test_missing_file_is_noop(tmp_path):
    assert load_dotenv(tmp_path / "nope.env") == 0
