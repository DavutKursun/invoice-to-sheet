"""BudgetGuard: dollars and documents per UTC day, the friendly message, check(), robustness, thread safety."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from invoice_to_sheet.costs import PRICES_PER_MTOK, BudgetExceeded, BudgetGuard, Spend, cost_usd, format_cost, is_priced


def test_price_helpers_unchanged():
    assert set(PRICES_PER_MTOK) == {"claude-haiku-5-5", "claude-sonnet-5-5"}
    assert is_priced("claude-haiku-5-5") and not is_priced("qwen/qwen3.8-27b")
    assert cost_usd("claude-haiku-5-5", 1500, 200) == pytest.approx(0.00025) and format_cost("claude-haiku-5-5", 0.00025) == "$0.0003"
    assert cost_usd("qwen/qwen3.8-27b", 1500, 200) == 0.0 and format_cost("qwen/qwen3.8-27b", 0.0) == "free tier"


def test_dollars_are_tracked_per_utc_day_with_a_fake_clock(tmp_path: Path):
    clock = {"t": 1_800_000_000.0}  # 2027-01-15 08:00 UTC
    path = tmp_path / "budget.json"
    guard = BudgetGuard(path, max_daily_usd=0.01, clock=lambda: clock["t"])
    assert guard.can_spend() and guard.remaining_usd() == pytest.approx(0.01) and guard.remaining_docs() is None
    guard.record(0.004)
    s = guard.record(0.004)
    assert s.date == "2027-01-15" and s.usd == pytest.approx(0.008) and s.calls == 2
    d = json.loads(path.read_text(encoding="utf-8"))
    assert set(d) == {"date", "usd", "calls"} and d["date"] == "2027-01-15" and d["usd"] == pytest.approx(0.008) and d["calls"] == 2
    assert guard.remaining_usd() == pytest.approx(0.002)
    assert guard.can_spend(0.001) and not guard.can_spend(0.005)
    guard.record(0.004)
    assert not guard.can_spend() and guard.remaining_usd() == 0.0
    clock["t"] += 86_400  # the next UTC day: the counters reset by themselves
    assert guard.can_spend() and guard.read() == Spend("2027-01-16", 0.0, 0) and guard.remaining_usd() == pytest.approx(0.01)
    assert guard.record(0.001).calls == 1


def test_document_cap_counts_free_documents_too(tmp_path: Path):
    guard = BudgetGuard(tmp_path / "b.json", max_daily_usd=100.0, max_daily_docs=3)
    assert guard.remaining_docs() == 3 and not guard.docs_exhausted() and guard.can_spend()
    for _ in range(3):
        guard.record(0.0)  # free-tier documents cost nothing but still count
    assert guard.remaining_docs() == 0 and guard.docs_exhausted() and not guard.can_spend()
    assert guard.read().calls == 3 and guard.remaining_usd() == 100.0
    guard.record(0.0)  # a document that slipped through still counts; "remaining" never goes negative
    assert guard.read().calls == 4 and guard.remaining_docs() == 0
    assert BudgetGuard(tmp_path / "c.json", max_daily_usd=1.0, max_daily_docs=0).remaining_docs() is None  # 0 = no cap
    assert BudgetGuard(tmp_path / "c.json", max_daily_usd=1.0, max_daily_docs=None).remaining_docs() is None
    uncapped = BudgetGuard(tmp_path / "c.json", max_daily_usd=1.0)
    assert uncapped.max_daily_docs is None and not uncapped.docs_exhausted()


def test_exhausted_message_names_the_cap_that_was_hit(tmp_path: Path):
    docs = BudgetGuard(tmp_path / "docs.json", max_daily_usd=0.5, max_daily_docs=2)
    docs.record(0.1)
    docs.record(0.1)
    assert docs.exhausted_message() == "Daily demo budget reached (2 of 2 documents today); resets at 00:00 UTC."
    dollars = BudgetGuard(tmp_path / "usd.json", max_daily_usd=0.5, max_daily_docs=100)
    dollars.record(0.5)
    assert dollars.exhausted_message() == "Daily demo budget reached ($0.50 of $0.50 spent today); resets at 00:00 UTC."
    for msg in (docs.exhausted_message(), dollars.exhausted_message()):
        assert msg.startswith("Daily demo budget reached") and msg.endswith("resets at 00:00 UTC.")


def test_check_raises_budget_exceeded_with_the_friendly_message(tmp_path: Path):
    guard = BudgetGuard(tmp_path / "b.json", max_daily_usd=0.02)
    guard.check()  # a fresh day
    guard.record(0.012)
    guard.check(0.005)  # 0.012 + 0.005 still fits
    with pytest.raises(BudgetExceeded) as info:
        guard.check()  # the default estimate is one cent: 0.012 + 0.01 > 0.02
    assert str(info.value) == "Daily demo budget reached ($0.01 of $0.02 spent today); resets at 00:00 UTC."
    assert isinstance(info.value, RuntimeError)  # callers that catch RuntimeError keep working
    assert guard.read().calls == 1  # a refused check records nothing
    capped = BudgetGuard(tmp_path / "c.json", max_daily_usd=5.0, max_daily_docs=1)
    capped.check(0.0)
    capped.record(0.0)
    with pytest.raises(BudgetExceeded, match=r"^Daily demo budget reached \(1 of 1 documents today\); resets at 00:00 UTC\.$"):
        capped.check(0.0)


def test_corrupt_missing_or_stale_file_counts_as_a_fresh_day(tmp_path: Path):
    path = tmp_path / "budget.json"
    guard = BudgetGuard(path, max_daily_usd=1.0, max_daily_docs=5)
    assert guard.read().calls == 0 and guard.can_spend()  # missing
    path.write_text("{not json", encoding="utf-8")
    assert guard.read() == Spend(guard._today(), 0.0, 0) and guard.remaining_docs() == 5
    assert guard.record(0.25).calls == 1  # and the file is valid again
    assert json.loads(path.read_text(encoding="utf-8")) == {"date": guard._today(), "usd": 0.25, "calls": 1}
    path.write_text(json.dumps({"date": "2001-01-01", "usd": 9.0, "calls": 9}), encoding="utf-8")
    assert guard.read().usd == 0.0 and guard.can_spend()  # yesterday's counters do not count
    path.write_text("[1, 2]", encoding="utf-8")
    assert guard.read().calls == 0 and guard.can_spend()


def test_concurrent_record_loses_no_increment(tmp_path: Path):
    path = tmp_path / "budget.json"
    n_threads, per_thread = 8, 25

    def work():
        guard = BudgetGuard(path, max_daily_usd=100.0, max_daily_docs=1000)  # one guard per handler, like the app
        for _ in range(per_thread):
            guard.record(0.001)

    threads = [threading.Thread(target=work) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    s = BudgetGuard(path, max_daily_usd=100.0, max_daily_docs=1000).read()
    assert s.calls == n_threads * per_thread and s.usd == pytest.approx(n_threads * per_thread * 0.001)
    assert not list(tmp_path.glob(".budget-*.tmp"))  # no temp files left behind
