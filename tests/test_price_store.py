#!/usr/bin/env python3
"""`scripts/price_store.py` reports what the store cost — ADR-0024 §SD2.

ADR-0022's § Context figures were produced by a design-time script that omitted
`cache_read`, the one token class the ADR existed to add. The script was never
committed, so the figures could not be re-run, and the error survived to merge.
ADR-0024 commits the measurement instead. These tests hold it to the one rule
that makes it trustworthy:

  **it must not re-derive cost.** `price_store()` reports what
  `claude_store.read_session()` already computed. The moment it grows its own
  accumulation loop, it becomes a second implementation that can be wrong on
  its own — which is the whole defect, rebuilt one directory over.

`test_report_total_equals_the_store_readers_own_sum` is that guard, and the
fixture is weighted the way real sessions are — cache reads dwarfing fresh input
and output — so a re-implementation that drops `cache_read` the way ADR-0022's
script did fails by 5x, not by a rounding error.

Run:
    python3 -m pytest projects/switchboard/repos/switchboard/tests/test_price_store.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402

import price_store  # noqa: E402
from control_plane import claude_store, pricing  # noqa: E402

M = 1_000_000

REGISTRY_JSON = {
    "cache_multipliers": {"write_5m": 1.25, "write_1h": 2.0, "read": 0.1},
    "aliases": {},
    "models": {
        "claude-opus-5": {"input": 5.00, "output": 25.00},
        "claude-sonnet-5": {"input": 3.00, "output": 15.00},
    },
    "default_fallback": None,
}


@pytest.fixture(autouse=True)
def _isolate_pricing():
    """`set_pricing` is a module-level singleton — don't leak it into the suite."""
    pricing.reset_unpriced_log()
    yield
    claude_store.set_pricing(None)
    claude_store.invalidate_summary_cache()


@pytest.fixture
def pricing_path(tmp_path):
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps(REGISTRY_JSON), encoding="utf-8")
    return path


def _assistant(model, *, inp=0, out=0, write_1h=0, read=0, ts="2026-07-26T00:00:00.000Z"):
    return {
        "type": "assistant",
        "sessionId": "placeholder",
        "cwd": "/workspaces/my-projects",
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": "ok"}],
            "usage": {
                "input_tokens": inp,
                "output_tokens": out,
                "cache_read_input_tokens": read,
                "cache_creation_input_tokens": write_1h,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 0,
                    "ephemeral_1h_input_tokens": write_1h,
                },
            },
        },
    }


def _write_session(root: Path, project: str, session_id: str, events: list[dict]) -> Path:
    proj = root / project
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / f"{session_id}.jsonl"
    for ev in events:
        ev["sessionId"] = session_id
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )
    return path


@pytest.fixture
def store(tmp_path):
    """A store shaped like the real one: priced, unpriced, partial, and empty.

    Cache-heavy by design — `cache_read` is ~95% of the bill here, so an
    implementation that drops it cannot pass by coincidence.
    """
    root = tmp_path / "projects"
    _write_session(
        root,
        "-workspaces-my-projects",
        "aaaaaaaa-0000",
        [_assistant("claude-opus-5", inp=100, out=100_000, write_1h=1 * M, read=100 * M)],
    )
    _write_session(
        root,
        "-workspaces-my-projects",
        "bbbbbbbb-0000",
        [_assistant("deepseek-v4-pro", inp=5 * M, read=10 * M)],
    )
    _write_session(
        root,
        "-workspaces-other",
        "cccccccc-0000",
        [
            _assistant("claude-sonnet-5", inp=1 * M),
            _assistant("deepseek-v4-pro", inp=1 * M),
        ],
    )
    _write_session(
        root,
        "-workspaces-other",
        "dddddddd-0000",
        [
            {
                "type": "user",
                "cwd": "/tmp",
                "timestamp": "2026-07-26T00:00:00.000Z",
                "message": {"role": "user", "content": "hi"},
            }
        ],
    )
    return root


# --- The anti-drift guard (ADR-0024 § Handoff step 2) ------------------------


def test_report_total_equals_the_store_readers_own_sum(store, pricing_path):
    """The report must be a *view* of read_session(), never a second calculation."""
    report = price_store.price_store(store, pricing_path)

    registry = pricing.load_pricing(str(pricing_path))
    claude_store.set_pricing(registry)
    claude_store.invalidate_summary_cache()
    independent = sum(
        claude_store.read_session(p).total_cost_usd or 0.0
        for p in sorted(store.glob("*/*.jsonl"))
    )

    assert report.total == pytest.approx(independent)


def test_cache_read_reaches_the_total(store, pricing_path):
    """The ADR-0022 defect, as a canary.

    Its design-time script priced the store without `cache_read`. The fixture is
    weighted the way real sessions are — cache reads dwarf fresh input and
    output — so repeating that omission is a 5x error here, not a rounding one.
    """
    report = price_store.price_store(store, pricing_path)

    # Session A: (100 + 1M*2.0 + 100M*0.1) * $5/1M + 100k * $25/1M = $62.50
    # Session C: 1M * $3/1M                                        = $ 3.00
    assert report.total == pytest.approx(65.5005), (
        "the store total no longer matches hand-computed ADR-0022 §SD3 arithmetic"
    )

    # The same store priced the way ADR-0022's script did: $15.50, not $65.50.
    without_cache_read = ((100 + 1 * M * 2.0) * 5.00 + 100_000 * 25.00) / 1e6 + 3.00
    assert report.total > without_cache_read * 4, (
        f"total ${report.total:.2f} sits near the cache_read-omitted basis "
        f"(${without_cache_read:.2f}) — the ADR-0022 arithmetic is back"
    )


# --- Report shape ------------------------------------------------------------


def test_report_separates_priced_unpriced_and_no_usage(store, pricing_path):
    report = price_store.price_store(store, pricing_path)
    assert report.priced_count == 2  # opus session + the mixed one
    assert report.unpriced_count == 1  # deepseek-only
    assert report.no_usage_count == 1  # user turns only
    assert report.unpriced_models == ["deepseek-v4-pro"]


def test_partial_session_is_flagged_not_silently_summed(store, pricing_path):
    report = price_store.price_store(store, pricing_path)
    mixed = next(s for s in report.sessions if s.session_id == "cccccccc-0000")
    assert mixed.partial is True
    assert mixed.unpriced_models == ["deepseek-v4-pro"]
    assert mixed.cost == pytest.approx(3.00)  # the sonnet half only


def test_sessions_are_ordered_by_cost_descending(store, pricing_path):
    report = price_store.price_store(store, pricing_path)
    priced = [s.cost for s in report.sessions if s.cost is not None]
    assert priced == sorted(priced, reverse=True)


def test_empty_store_reports_zero_not_a_crash(tmp_path, pricing_path):
    report = price_store.price_store(tmp_path / "nothing-here", pricing_path)
    assert report.total == 0.0
    assert report.sessions == []


def test_missing_pricing_file_raises(store, tmp_path):
    """Cost disabled silently is the failure mode this whole ADR line is about."""
    with pytest.raises(FileNotFoundError):
        price_store.price_store(store, tmp_path / "no-such-pricing.json")


# --- Rendering ---------------------------------------------------------------


def test_render_marks_unpriced_distinctly_from_zero(store, pricing_path):
    """`unpriced` and `$0.00` are different facts (ADR-0022 §SD2) — on the CLI too."""
    text = price_store.render(price_store.price_store(store, pricing_path))
    assert "unpriced" in text
    assert "deepseek-v4-pro" in text
    assert "$0.00" not in text


def test_render_states_provenance(store, pricing_path):
    """ADR-0024 §SD2: a figure travels with the command that produced it."""
    text = price_store.render(price_store.price_store(store, pricing_path))
    assert "price_store.py" in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
