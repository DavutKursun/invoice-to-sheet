"""Settings: the .env loader, the Space-mode defaults and the "an explicit variable wins" rule."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from invoice_to_sheet.config import Settings, _load_dotenv, load_dotenv_unless_on_space, on_space

ROOT = Path(__file__).resolve().parents[1]
# every variable the Space defaults touch; the local .env is already merged into os.environ when the tests run
SPACE_KEYS = ("SPACE_ID", "LLM_PROVIDER", "ANTHROPIC_MODEL", "MAX_PAGES", "MAX_FILE_MB", "MAX_DAILY_USD", "MAX_DAILY_DOCS",
              "BUDGET_FILE", "MAX_FILES_PER_BATCH", "DOCS_FAKE_EXTRACTOR")


def _forget(monkeypatch, *keys: str) -> None:
    """Drop keys from the environment for this test; the originals come back afterwards even if a loader re-adds them."""
    for key in keys:
        monkeypatch.setenv(key, "")  # records the original value (or its absence) for the restore
        monkeypatch.delenv(key)


def _keys_in(path: Path) -> list[str]:
    keys = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            keys.append(line.partition("=")[0].strip())
    return keys


def test_loader_strips_quotes_and_inline_comments_and_never_overrides(tmp_path: Path, monkeypatch):
    keys = ["ITS_T_PLAIN", "ITS_T_COMMENT", "ITS_T_DQ", "ITS_T_SQ", "ITS_T_HASH_IN_QUOTES", "ITS_T_HASH_NO_SPACE", "ITS_T_EMPTY",
            "ITS_T_ONLY_COMMENT", "ITS_T_EQUALS", "ITS_T_PRESET"]
    _forget(monkeypatch, *keys)
    monkeypatch.setenv("ITS_T_PRESET", "from-the-environment")
    env = tmp_path / ".env"
    env.write_text(
        "# a comment line\n"
        "\n"
        "ITS_T_PLAIN = openai_compat \n"
        "ITS_T_COMMENT=qwen/qwen3.8-27b        # a vision-capable model id\n"
        'ITS_T_DQ="sk-ant with spaces"\n'
        "ITS_T_SQ='single # not a comment'\n"
        'ITS_T_HASH_IN_QUOTES="a #b"\n'
        "ITS_T_HASH_NO_SPACE=a#b\n"
        "ITS_T_EMPTY=\n"
        "ITS_T_ONLY_COMMENT=      # base64 of the service-account JSON file\n"
        "ITS_T_EQUALS=a=b=c   # keeps every = after the first\n"
        "ITS_T_PRESET=from-the-file\n"
        "not a key value line\n",
        encoding="utf-8",
    )
    _load_dotenv(env)
    assert os.environ["ITS_T_PLAIN"] == "openai_compat"
    assert os.environ["ITS_T_COMMENT"] == "qwen/qwen3.8-27b"
    assert os.environ["ITS_T_DQ"] == "sk-ant with spaces"
    assert os.environ["ITS_T_SQ"] == "single # not a comment"
    assert os.environ["ITS_T_HASH_IN_QUOTES"] == "a #b"
    assert os.environ["ITS_T_HASH_NO_SPACE"] == "a#b"
    assert os.environ["ITS_T_EMPTY"] == ""
    assert os.environ["ITS_T_ONLY_COMMENT"] == ""
    assert os.environ["ITS_T_EQUALS"] == "a=b=c"
    assert os.environ["ITS_T_PRESET"] == "from-the-environment"  # setdefault: a real variable always wins
    _load_dotenv(tmp_path / "missing.env")  # a missing file is a no-op


def test_env_example_loads_cleanly_and_builds_the_local_settings(monkeypatch):
    example = ROOT / ".env.example"
    keys = _keys_in(example)
    assert {"LLM_PROVIDER", "MAX_DAILY_USD", "MAX_DAILY_DOCS", "BUDGET_FILE", "DOCS_FAKE_EXTRACTOR"} <= set(keys)
    _forget(monkeypatch, "SPACE_ID", *keys)
    _load_dotenv(example)
    assert os.environ["LLM_PROVIDER"] == "openai_compat" and os.environ["LLM_MODEL"] == "" and os.environ["USE_JSON_SCHEMA"] == "0"
    assert os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] == "" and os.environ["ANTHROPIC_MODEL"] == "claude-sonnet-5-5"
    assert os.environ["MAX_DAILY_DOCS"] == "0" and os.environ["BUDGET_FILE"] == ".budget.json" and os.environ["DOCS_FAKE_EXTRACTOR"] == "0"
    s = Settings()
    assert (s.llm_provider, s.anthropic_model, s.max_pages, s.max_file_mb) == ("openai_compat", "claude-sonnet-5-5", 4, 10)
    assert (s.max_daily_usd, s.max_daily_docs, s.budget_file, s.max_files_per_batch) == (2.0, 0, Path(".budget.json"), 10)
    assert s.use_json_schema is False and s.fake_extractor is False and s.on_space is False


def test_space_defaults_apply_only_when_space_id_is_set(monkeypatch):
    _forget(monkeypatch, *SPACE_KEYS)
    local = Settings()
    assert not on_space() and not local.on_space
    assert (local.llm_provider, local.anthropic_model, local.max_pages, local.max_file_mb) == ("openai_compat", "claude-sonnet-5-5", 4, 10)
    assert (local.max_daily_usd, local.max_daily_docs, local.budget_file, local.max_files_per_batch) == (2.0, 0, Path(".budget.json"), 10)
    assert local.fake_extractor is False

    monkeypatch.setenv("SPACE_ID", "someone/invoice-to-sheet")
    space = Settings()
    assert on_space() and space.on_space
    assert (space.llm_provider, space.anthropic_model, space.max_pages, space.max_file_mb) == ("anthropic", "claude-haiku-5-5", 20, 10)
    assert (space.max_daily_usd, space.max_daily_docs, space.max_files_per_batch) == (0.5, 100, 10)
    assert space.budget_file == Path(tempfile.gettempdir()) / "invoice-to-sheet" / "budget.json"
    assert space.fake_extractor is False
    # knobs the Space does not touch keep their local values
    assert (space.render_dpi, space.max_image_side, space.min_confidence) == (local.render_dpi, local.max_image_side, local.min_confidence)


def test_an_explicit_variable_wins_on_a_space(monkeypatch):
    _forget(monkeypatch, *SPACE_KEYS)
    monkeypatch.setenv("SPACE_ID", "someone/invoice-to-sheet")
    monkeypatch.setenv("MAX_DAILY_DOCS", "38")
    monkeypatch.setenv("MAX_DAILY_USD", "1.25")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("BUDGET_FILE", "/data/budget.json")
    monkeypatch.setenv("DOCS_FAKE_EXTRACTOR", "true")
    s = Settings()
    assert (s.max_daily_docs, s.max_daily_usd, s.anthropic_model) == (38, 1.25, "claude-sonnet-5-5")
    assert s.budget_file == Path("/data/budget.json")
    assert s.fake_extractor is True and s.llm_provider == "anthropic" and s.max_pages == 20  # the rest stays at the Space defaults
    assert Settings(max_pages=3).max_pages == 3  # keyword overrides still work (the tests build Settings this way)


def test_dotenv_is_not_read_on_a_space(tmp_path: Path, monkeypatch):
    _forget(monkeypatch, "SPACE_ID", "ITS_T_SPACE")
    env = tmp_path / ".env"
    env.write_text("ITS_T_SPACE=from-the-file\n", encoding="utf-8")
    monkeypatch.setenv("SPACE_ID", "someone/invoice-to-sheet")
    assert load_dotenv_unless_on_space(env) is False and "ITS_T_SPACE" not in os.environ
    monkeypatch.delenv("SPACE_ID")
    assert load_dotenv_unless_on_space(env) is True and os.environ["ITS_T_SPACE"] == "from-the-file"


def test_secrets_never_appear_in_the_settings_repr():
    s = Settings(llm_api_key="gsk_secret_value", anthropic_api_key="sk-ant-secret-value", google_service_account_json_b64="eyJzZWNyZXQ=")
    text = repr(s)
    assert "gsk_secret_value" not in text and "sk-ant-secret-value" not in text and "eyJzZWNyZXQ=" not in text
    assert s.anthropic_api_key == "sk-ant-secret-value"  # the values are still there, just not printed
