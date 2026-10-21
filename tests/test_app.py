"""The Gradio app: pure helpers, the UI wiring (nothing is launched) and the handlers on the fake extractor.

Skipped where Gradio is not installed (CI installs requirements-dev.txt only). No network: the fake extractor
copies the fields from the dataset labels, so the three sample documents extract deterministically.
"""

from __future__ import annotations

import csv
import inspect
import json
import os
import tempfile
import time
from pathlib import Path

import pytest

gr = pytest.importorskip("gradio")

import app  # noqa: E402
from invoice_to_sheet.config import Settings  # noqa: E402
from invoice_to_sheet.pipeline import ProcessedDoc  # noqa: E402
from invoice_to_sheet.sheets import COLUMNS  # noqa: E402

CLEAN, PHOTO, TRICKY = (str(app.SAMPLES[k]) for k in ("clean", "photo", "tricky"))


def fake_cfg(tmp_path: Path, **over) -> Settings:
    kw = dict(
        fake_extractor=True, llm_provider="anthropic", anthropic_model="claude-haiku-5-5", anthropic_api_key="",
        budget_file=tmp_path / "budget.json", max_daily_docs=2, max_daily_usd=2.0, max_files_per_batch=10, max_pages=20, max_file_mb=10,
    )
    kw.update(over)
    return Settings(**kw)


def is_skip(x) -> bool:
    return x == {"__type__": "update"}


def button_args(x) -> dict:
    """What a returned ``gr.DownloadButton(...)`` carries (Gradio turns it into an update after the handler returns)."""
    return x.constructor_args if isinstance(x, gr.DownloadButton) else dict(x)


def csv_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --- configuration helpers -------------------------------------------------------------------------------------


def test_key_configured_model_id_and_banner():
    claude = Settings(fake_extractor=False, llm_provider="anthropic", anthropic_api_key="k", anthropic_model="claude-haiku-5-5")
    no_key = Settings(fake_extractor=False, llm_provider="anthropic", anthropic_api_key="")
    groq = Settings(fake_extractor=False, llm_provider="openai_compat", llm_api_key="k", llm_model="qwen/qwen3.8-27b")
    groq_no_model = Settings(fake_extractor=False, llm_provider="openai_compat", llm_api_key="k", llm_model="")
    fake = Settings(fake_extractor=True, llm_provider="openai_compat", llm_api_key="", llm_model="")
    assert app.key_configured(claude) and app.key_configured(groq) and app.key_configured(fake)
    assert not app.key_configured(no_key) and not app.key_configured(groq_no_model)
    assert app.model_id(claude) == "claude-haiku-5-5" and app.model_id(groq) == "qwen/qwen3.8-27b" and app.model_id(groq_no_model) == ""
    assert app.banner_text(claude) is None
    assert "Demo key not configured" in app.banner_text(no_key) and "ANTHROPIC_API_KEY" in app.banner_text(no_key)
    assert "Fake extractor active" in app.banner_text(fake)


def test_status_text_shows_model_budget_and_limits(tmp_path: Path):
    cfg = Settings(
        fake_extractor=False, llm_provider="anthropic", anthropic_api_key="k", anthropic_model="claude-haiku-5-5",
        budget_file=tmp_path / "b.json", max_daily_docs=100, max_daily_usd=0.5, max_file_mb=10, max_pages=20, max_files_per_batch=10,
    )
    assert app.status_text(cfg) == (
        "Model `claude-haiku-5-5` via anthropic · documents left today: 100 of 100 · budget left today: $0.50 of $0.50 · "
        "limits: 10 MB per file, 20 pages, 10 files per batch"
    )
    app.budget_guard(cfg).record(0.01)
    assert "documents left today: 99 of 100 · budget left today: $0.49 of $0.50" in app.status_text(cfg)
    assert "documents left today: unlimited" in app.status_text(fake_cfg(tmp_path, max_daily_docs=0))
    assert "via fake" in app.status_text(fake_cfg(tmp_path))


def test_health_payload_shape(tmp_path: Path):
    cfg = fake_cfg(tmp_path, max_daily_docs=100, max_daily_usd=0.5)
    payload = app.health_payload(cfg)
    assert set(payload) >= {"status", "version", "model", "provider", "key_configured", "fake_extractor", "on_space", "limits", "budget"}
    assert payload["status"] == "ok" and payload["provider"] == "fake" and payload["fake_extractor"] is True and payload["key_configured"] is True
    assert payload["model"] == "claude-haiku-5-5" and isinstance(payload["on_space"], bool)
    assert payload["limits"] == {"file_mb": 10, "pages": 20, "files_per_batch": 10, "docs_per_day": 100, "usd_per_day": 0.5}
    assert payload["budget"] == {"docs_today": 0, "usd_today": 0.0, "docs_left_today": 100, "usd_left_today": 0.5}
    assert app.health_payload(fake_cfg(tmp_path, max_daily_docs=0))["budget"]["docs_left_today"] is None
    json.dumps(payload)  # what /health sends


def test_provider_and_model_serves_the_fake_and_caches_it(tmp_path: Path):
    cfg = fake_cfg(tmp_path)
    provider, model = app.provider_and_model(cfg)
    assert provider.name == "fake" and model == "claude-haiku-5-5"
    assert app.provider_and_model(cfg)[0] is provider


# --- UI wiring (nothing launched) -------------------------------------------------------------------------------


def test_build_ui_without_launching(tmp_path: Path):
    demo = app.build_ui(fake_cfg(tmp_path))
    assert isinstance(demo, gr.Blocks)
    config = demo.get_config_file()
    kinds = [c["type"] for c in config["components"]]
    assert kinds.count("file") == 2 and kinds.count("image") == 1 and kinds.count("dataframe") == 3 and kinds.count("html") == 1
    assert kinds.count("button") == 6 and kinds.count("downloadbutton") == 2 and kinds.count("tabitem") == 2 and "accordion" in kinds
    markdown = [str(c["props"].get("value", "")) for c in config["components"] if c["type"] == "markdown"]
    assert any("Fake extractor active" in m for m in markdown) and any("documents left today" in m for m in markdown)
    labels = {c["props"].get("label") for c in config["components"]}
    assert "PDF or image (max 10 MB, 20 pages)" in labels and "Up to 10 files" in labels
    assert {d.get("api_name") for d in config["dependencies"]} >= {"load", "extract", "sample_clean", "sample_photo", "sample_tricky", "append", "batch"}
    assert config["title"] == app.TITLE
    fastapi_app = gr.routes.App.create_app(demo)  # what launch() builds; /health is added to it
    app.add_health_route(fastapi_app)
    assert any(getattr(r, "path", "") == "/health" for r in fastapi_app.routes)


def test_banner_only_without_a_key(tmp_path: Path):
    config = app.build_ui(fake_cfg(tmp_path, fake_extractor=False, anthropic_api_key="k")).get_config_file()
    markdown = [str(c["props"].get("value", "")) for c in config["components"] if c["type"] == "markdown"]
    assert not any("⚠️" in m for m in markdown)
    config = app.build_ui(fake_cfg(tmp_path, fake_extractor=False, anthropic_api_key="")).get_config_file()
    markdown = [str(c["props"].get("value", "")) for c in config["components"] if c["type"] == "markdown"]
    assert any("Demo key not configured" in m for m in markdown)


def test_bound_handlers_hide_cfg_from_gradio(tmp_path: Path):
    """Gradio injects gr.Progress positionally and fills trailing defaulted parameters, so cfg must stay invisible."""
    from gradio import helpers

    cfg = fake_cfg(tmp_path, max_daily_docs=0)
    for fn, n_inputs in ((app.extract_single, 2), (app.run_batch, 2), (app.append_to_csv, 4), (app.on_load, 1)):
        bound = app._bind(fn, cfg)
        assert "cfg" not in inspect.signature(bound).parameters and bound.__name__ == fn.__name__
        inputs = [None] * n_inputs
        helpers.special_args(bound, inputs=inputs)
        assert not any(isinstance(x, Settings) for x in inputs)
    with pytest.warns(UserWarning, match="Upload up to"):  # exactly what Gradio passes: inputs, then the Progress
        assert all(is_skip(x) for x in app._bind(app.run_batch, cfg)(None, "s", gr.Progress()))


def test_cleanup_sessions_prunes_folders_older_than_an_hour(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    root = app.sessions_root()
    stale = time.time() - 2 * 3600
    old, fresh = root / "old", root / "fresh"
    for folder in (old, fresh):
        folder.mkdir(parents=True)
        (folder / "invoices.csv").write_text("x", encoding="utf-8")
    os.utime(old / "invoices.csv", (stale, stale))
    os.utime(old, (stale, stale))
    assert app.cleanup_sessions() == 1 and not old.exists() and fresh.exists()
    assert app.cleanup_sessions() == 0  # nothing left to prune; a missing root is fine too
    assert app.cleanup_sessions(root=tmp_path / "nowhere") == 0

    old.mkdir()  # every request prunes: a page load is enough
    (old / "review.csv").write_text("y", encoding="utf-8")
    os.utime(old / "review.csv", (stale, stale))
    os.utime(old, (stale, stale))
    app.on_load(None, cfg=fake_cfg(tmp_path))
    assert not old.exists() and fresh.exists()
    assert app.build_ui(fake_cfg(tmp_path)).delete_cache == (3600, 3600)  # Gradio's upload cache, same schedule


# --- table and text helpers -------------------------------------------------------------------------------------


def test_fields_table_round_trips_through_table_to_extraction(good_extraction):
    rows = app.fields_table(good_extraction)
    assert [r[0] for r in rows] == app.FIELD_ROWS
    assert ["vendor_name", "Aria Air GmbH"] in rows and ["total", "119.0"] in rows and ["low_confidence_fields", ""] in rows
    lines = app.line_rows(good_extraction)
    assert lines[0] == ["Filter", 2.0, 25.0, 50.0]
    assert app.table_to_extraction(rows, lines, good_extraction) == good_extraction

    pd = pytest.importorskip("pandas")  # what gr.Dataframe delivers to a handler; edited cells arrive as strings
    edited = pd.DataFrame([[k, "125.5" if k == "total" else v] for k, v in rows], columns=["field", "value"])
    back = app.table_to_extraction(edited, pd.DataFrame(lines, columns=app.LINE_HEADERS), good_extraction)
    assert back.total == 125.5 and back.line_items == good_extraction.line_items and back.vendor_name == "Aria Air GmbH"

    partial = app.table_to_extraction([["low_confidence_fields", "total, subtotal"], ["document_type", "Receipt"]], [], good_extraction)
    assert partial.low_confidence_fields == ["total", "subtotal"] and partial.document_type == "receipt"
    assert partial.line_items == good_extraction.line_items  # an empty line table keeps the extracted items

    with pytest.raises(ValueError, match="total"):
        app.table_to_extraction([["total", "12,5 EUR"]], lines, good_extraction)
    with pytest.raises(ValueError, match="confidence"):
        app.table_to_extraction([["confidence", "7"]], lines, good_extraction)
    with pytest.raises(ValueError, match="document_type"):
        app.table_to_extraction([["document_type", "memo"]], lines, good_extraction)


def test_decision_html_escapes_and_lists_reasons():
    ok = app.decision_html("auto_approved", [])
    assert "auto_approved" in ok and "<ul>" not in ok
    bad = app.decision_html("needs_review", ["line 1: 2 × 3.00 ≠ 7.00", "<script>alert(1)</script>"])
    assert "needs_review" in bad and "<li>line 1: 2 × 3.00 ≠ 7.00</li>" in bad
    assert "&lt;script&gt;" in bad and "<script>" not in bad
    assert ok.split("background:")[1][:7] != bad.split("background:")[1][:7]  # green vs amber


def test_cost_line(good_extraction):
    out = ProcessedDoc("a.pdf", "auto_approved", [], good_extraction, "claude-haiku-5-5", 0.00091, "$0.0009", 4.81, False, 1)
    assert app.cost_line(out) == "cost $0.0009 · 4.8 s · claude-haiku-5-5 · 1 page"
    out.repaired, out.pages = True, 3
    assert app.cost_line(out) == "cost $0.0009 · 4.8 s · claude-haiku-5-5 · 3 pages · repaired once"
    cut = ProcessedDoc(
        "a.pdf", "needs_review", ["only the first 4 of 30 pages were read"], good_extraction, "m", 0.0, "free tier", 12.0, False, 30,
        ["a.pdf: 30 pages, only the first 4 were read"],
    )
    assert app.cost_line(cut) == "cost free tier · 12.0 s · m · 4 pages of 30 read"


# --- handlers on the fake extractor -----------------------------------------------------------------------------


def test_extract_single_on_the_fake_extractor_then_the_daily_cap(tmp_path: Path):
    cfg = fake_cfg(tmp_path, max_daily_docs=2)
    res = app.extract_single(CLEAN, "s1", cfg=cfg)
    assert len(res) == 8
    preview, fields, lines, decision, cost, download, state, status = res
    assert hasattr(preview, "size") and preview.size[0] > 100
    assert ["vendor_name", "Kestrel Software OÜ"] in fields and ["total", "2028.4"] in fields and ["currency", "EUR"] in fields
    assert len(lines) == 6 and lines[0][0] == "Safety vest, class 2"
    assert "auto_approved" in decision and "$" in cost and "claude-haiku-5-5" in cost and cost.endswith("1 page")
    assert button_args(download) == {"visible": False}
    assert state["file_name"] == "inv010.pdf" and state["decision"] == "auto_approved" and state["reasons"] == []
    assert state["data"]["total"] == 2028.4 and state["model"] == "claude-haiku-5-5" and state["cost_usd"] > 0 and state["pages"] == 1
    assert "documents left today: 1 of 2" in status

    res = app.extract_single(PHOTO, "s1", cfg=cfg)
    assert res[6]["data"]["vendor_name"] == "Kestrel Software OÜ" and res[6]["data"]["total"] == 1233.99
    assert "documents left today: 0 of 2" in res[7]

    with pytest.warns(UserWarning, match=r"Daily demo budget reached .*resets at 00:00 UTC"):
        res = app.extract_single(TRICKY, "s1", cfg=cfg)
    assert len(res) == 8 and all(is_skip(x) for x in res)
    assert app.budget_guard(cfg).read().calls == 2  # the third document was not processed


def test_extract_single_warns_instead_of_raising(tmp_path: Path):
    cfg = fake_cfg(tmp_path, max_daily_docs=0)
    with pytest.warns(UserWarning, match="Upload a PDF or image first"):
        assert all(is_skip(x) for x in app.extract_single(None, "s1", cfg=cfg))
    no_key = fake_cfg(tmp_path, fake_extractor=False, anthropic_api_key="")
    with pytest.warns(UserWarning, match="Demo key not configured"):
        assert all(is_skip(x) for x in app.extract_single(CLEAN, "s1", cfg=no_key))
    bad = tmp_path / "notes.txt"
    bad.write_text("not an invoice", encoding="utf-8")
    with pytest.warns(UserWarning, match="Unsupported file type"):
        assert all(is_skip(x) for x in app.extract_single(str(bad), "s1", cfg=cfg))
    assert app.budget_guard(cfg).read().calls == 0


def test_append_to_csv_writes_the_session_csv_and_flags_duplicates(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))  # session CSVs land under tmp_path
    cfg = fake_cfg(tmp_path, max_daily_docs=0)
    _, fields, lines, _, _, _, state, _ = app.extract_single(CLEAN, "sess1", cfg=cfg)

    html_out, download, status = app.append_to_csv(fields, lines, state, "sess1", cfg=cfg)
    args = button_args(download)
    assert args["visible"] is True and args["label"] == "Download invoices.csv"
    path = Path(args["value"])
    assert path.exists() and path.parent == tmp_path / "invoice-to-sheet" / "sessions" / "sess1"
    rows = csv_rows(path)
    assert len(rows) == 1 and list(rows[0]) == COLUMNS
    assert rows[0]["vendor_name"] == "Kestrel Software OÜ" and rows[0]["decision"] == "auto_approved" and rows[0]["model"] == "claude-haiku-5-5"
    assert json.loads(rows[0]["line_items"])[0]["description"] == "Safety vest, class 2" and "auto_approved" in html_out
    assert "documents left today" in status

    html2, download2, _ = app.append_to_csv(fields, lines, state, "sess1", cfg=cfg)  # same vendor + number again
    assert "needs_review" in html2 and "duplicate" in html2 and button_args(download2)["label"] == "Download review.csv"
    assert len(csv_rows(path)) == 1 and len(csv_rows(path.parent / "review.csv")) == 1

    edited = [[k, "999" if k == "total" else v] for k, v in fields]  # an edit that breaks the arithmetic
    html3, _, _ = app.append_to_csv(edited, lines, state, "sess2", cfg=cfg)
    assert "needs_review" in html3 and "≠ total 999.00" in html3

    with pytest.warns(UserWarning, match="Nothing extracted yet"):
        assert all(is_skip(x) for x in app.append_to_csv(fields, lines, None, "sess1", cfg=cfg))
    with pytest.warns(UserWarning, match="total"):
        assert all(is_skip(x) for x in app.append_to_csv([["total", "abc"]], lines, state, "sess1", cfg=cfg))


def test_run_batch_rejects_too_many_files_and_writes_batch_csv(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    cfg = fake_cfg(tmp_path, max_daily_docs=0, max_files_per_batch=2)
    with pytest.warns(UserWarning, match="limit is 2 per batch"):
        res = app.run_batch([CLEAN, PHOTO, TRICKY], "b1", cfg=cfg)
    assert len(res) == 3 and all(is_skip(x) for x in res)
    assert app.budget_guard(cfg).read().calls == 0  # nothing was processed

    summary, download, status = app.run_batch([CLEAN, PHOTO], "b1", cfg=cfg)
    assert [r[1] for r in summary] == ["auto_approved", "auto_approved"]
    assert summary[0][0] == "inv010.pdf" and summary[0][2] == 2028.4 and summary[0][3].startswith("$")
    path = Path(button_args(download)["value"])
    assert path.name == "batch.csv" and path.parent == tmp_path / "invoice-to-sheet" / "sessions" / "b1"
    rows = csv_rows(path)
    assert len(rows) == 2 and list(rows[0]) == COLUMNS and rows[1]["total"] == "1233.99"
    assert app.budget_guard(cfg).read().calls == 2 and "documents left today: unlimited" in status

    capped = fake_cfg(tmp_path / "capped", max_daily_docs=1)  # the daily cap stops the loop; the rest is skipped
    with pytest.warns(UserWarning, match="Daily demo budget reached"):
        summary2, download2, _ = app.run_batch([CLEAN, PHOTO, TRICKY], "b2", cfg=capped)
    assert [r[1] for r in summary2] == ["auto_approved", "skipped", "skipped"]
    rows2 = csv_rows(Path(button_args(download2)["value"]))
    assert rows2[1]["decision"] == "skipped" and "Daily demo budget reached" in rows2[1]["reasons"]

    bad = tmp_path / "notes.txt"  # a bad file is one error row; the loop goes on
    bad.write_text("x", encoding="utf-8")
    summary3, _, _ = app.run_batch([str(bad), CLEAN], "b3", cfg=fake_cfg(tmp_path / "bad", max_daily_docs=0))
    assert [r[1] for r in summary3] == ["error", "auto_approved"]
    with pytest.warns(UserWarning, match="Upload up to"):
        assert all(is_skip(x) for x in app.run_batch(None, "b3", cfg=cfg))
