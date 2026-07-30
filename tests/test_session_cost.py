#!/usr/bin/env python3
"""Session cost accumulation — ADR-0022 §SD1 / §SD3 / §SD4.

`read_session()` used to keep `total_input` + `total_output` and the *first*
model it saw, then price the whole session at that model's rate with two of the
four billable token classes. These tests pin the replacement:

  - usage accumulates per model id, and the session cost is the sum (§SD4)
  - the `cache_creation` TTL split drives the write multipliers (§SD3)
  - an unpriced model is reported as unpriced, never as $0 (§SD2)
  - an envelope `total_cost_usd` still overrides the token-derived figure (§SD1)

Run:
    python3 -m pytest projects/switchboard/repos/switchboard/tests/test_session_cost.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from control_plane import claude_store, pricing  # noqa: E402

M = 1_000_000

REGISTRY_JSON = {
    "cache_multipliers": {"write_5m": 1.25, "write_1h": 2.0, "read": 0.1, "source": "https://example.test/caching", "checked_on": "2026-07-30"},
    "aliases": {},
    "models": {
        "claude-sonnet-5": {"input": 3.00, "output": 15.00, "source": "https://example.test/rates", "checked_on": "2026-07-30"},
        "claude-opus-5": {"input": 5.00, "output": 25.00, "source": "https://example.test/rates", "checked_on": "2026-07-30"},
    },
    "default_fallback": None,
}


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps(REGISTRY_JSON), encoding="utf-8")
    return pricing.load_pricing(str(path))


@pytest.fixture(autouse=True)
def _quiet_drift_guard():
    """Each test starts with a clean once-per-process log."""
    pricing.reset_unpriced_log()
    yield
    claude_store.set_pricing(None)


def _assistant(model, usage, ts="2026-07-26T00:00:00.000Z", **extra):
    ev = {
        "type": "assistant",
        "sessionId": "sess-cost",
        "cwd": "/workspaces/my-projects",
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "model": model,
            "usage": usage,
            "content": [{"type": "text", "text": "ok"}],
        },
    }
    ev.update(extra)
    return ev


def _usage(inp=0, out=0, write_5m=0, write_1h=0, read=0, flat_write=None):
    """A claude usage block. `flat_write` emits the pre-TTL-split shape."""
    block = {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": (
            flat_write if flat_write is not None else write_5m + write_1h
        ),
    }
    if flat_write is None:
        block["cache_creation"] = {
            "ephemeral_5m_input_tokens": write_5m,
            "ephemeral_1h_input_tokens": write_1h,
        }
    return block


def _session(tmp_path, events, name="sess.jsonl") -> Path:
    path = tmp_path / name
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n",
        encoding="utf-8",
    )
    return path


# --- §SD4: price per model, not per session ----------------------------------


def test_cost_sums_each_model_at_its_own_rate(tmp_path, registry):
    """First-model-wins billed a whole session at the opening model's rate."""
    claude_store.set_pricing(registry)
    path = _session(
        tmp_path,
        [
            _assistant("claude-sonnet-5", _usage(inp=1 * M)),
            _assistant("claude-opus-5", _usage(inp=1 * M)),
        ],
    )
    summary = claude_store.read_session(path)
    # sonnet 1M in = $3.00, opus 1M in = $5.00 — neither rate applied to both.
    assert summary.total_cost_usd == pytest.approx(8.00)
    assert summary.cost_partial is False
    assert summary.unpriced_models == []


def test_single_model_session_still_prices(tmp_path, registry):
    claude_store.set_pricing(registry)
    path = _session(
        tmp_path, [_assistant("claude-opus-5", _usage(inp=2 * M, out=1 * M))]
    )
    summary = claude_store.read_session(path)
    assert summary.total_cost_usd == pytest.approx(2 * 5.00 + 1 * 25.00)


# --- §SD3: cache-aware token basis -------------------------------------------


def test_cache_tokens_are_billed_with_ttl_multipliers(tmp_path, registry):
    claude_store.set_pricing(registry)
    path = _session(
        tmp_path,
        [
            _assistant(
                "claude-opus-5",
                _usage(inp=0, out=0, write_5m=1 * M, write_1h=1 * M, read=10 * M),
            )
        ],
    )
    summary = claude_store.read_session(path)
    # (1M*1.25 + 1M*2.0 + 10M*0.1) = 4.25M units * $5 = $21.25
    assert summary.total_cost_usd == pytest.approx(21.25)


def test_cache_read_alone_is_not_free(tmp_path, registry):
    """The 0.42%-of-basis defect: a cache-only turn used to cost $0."""
    claude_store.set_pricing(registry)
    path = _session(
        tmp_path, [_assistant("claude-opus-5", _usage(inp=2, read=10 * M))]
    )
    summary = claude_store.read_session(path)
    assert summary.total_cost_usd > 4.99


def test_flat_cache_creation_falls_back_to_the_5m_rate(tmp_path, registry):
    """Older transcripts carry only `cache_creation_input_tokens`."""
    claude_store.set_pricing(registry)
    path = _session(
        tmp_path, [_assistant("claude-opus-5", _usage(flat_write=1 * M))]
    )
    summary = claude_store.read_session(path)
    assert summary.total_cost_usd == pytest.approx(1.25 * 5.00)


def test_iterations_block_is_not_double_counted(tmp_path, registry):
    """`usage.iterations[]` repeats the same counts — only the top level bills."""
    claude_store.set_pricing(registry)
    usage = _usage(inp=1 * M)
    usage["iterations"] = [
        {"input_tokens": 1 * M, "output_tokens": 0, "cache_read_input_tokens": 0}
    ]
    path = _session(tmp_path, [_assistant("claude-opus-5", usage)])
    summary = claude_store.read_session(path)
    assert summary.total_cost_usd == pytest.approx(5.00)


# --- §SD2: unpriced is a state, not a zero -----------------------------------


def test_wholly_unpriced_session_reports_unpriced_not_zero(tmp_path, registry):
    claude_store.set_pricing(registry)
    path = _session(tmp_path, [_assistant("deepseek-v4-pro", _usage(inp=5 * M))])
    summary = claude_store.read_session(path)
    assert summary.total_cost_usd is None
    assert summary.unpriced_models == ["deepseek-v4-pro"]
    assert summary.cost_partial is False


def test_mixed_session_reports_the_priced_subtotal_and_flags_partial(
    tmp_path, registry
):
    claude_store.set_pricing(registry)
    path = _session(
        tmp_path,
        [
            _assistant("claude-opus-5", _usage(inp=1 * M)),
            _assistant("deepseek-v4-pro", _usage(inp=1 * M)),
        ],
    )
    summary = claude_store.read_session(path)
    assert summary.total_cost_usd == pytest.approx(5.00)
    assert summary.cost_partial is True
    assert summary.unpriced_models == ["deepseek-v4-pro"]


def test_session_without_usage_is_neither_priced_nor_unpriced(tmp_path, registry):
    """No usage data at all is a third state — the card shows no cost field."""
    claude_store.set_pricing(registry)
    path = _session(
        tmp_path,
        [
            {
                "type": "user",
                "sessionId": "sess-cost",
                "cwd": "/tmp",
                "timestamp": "2026-07-26T00:00:00.000Z",
                "message": {"role": "user", "content": "hi"},
            }
        ],
    )
    summary = claude_store.read_session(path)
    assert summary.total_cost_usd is None
    assert summary.unpriced_models == []
    assert summary.cost_partial is False


def test_synthetic_turns_are_never_billed(tmp_path, registry):
    """`<synthetic>` marks a locally-generated turn — no provider call, no cost."""
    claude_store.set_pricing(registry)
    path = _session(tmp_path, [_assistant("<synthetic>", _usage(inp=9 * M))])
    summary = claude_store.read_session(path)
    assert summary.total_cost_usd is None
    assert summary.unpriced_models == []


def test_pricing_disabled_yields_no_cost(tmp_path):
    claude_store.set_pricing(None)
    path = _session(tmp_path, [_assistant("claude-opus-5", _usage(inp=1 * M))])
    summary = claude_store.read_session(path)
    assert summary.total_cost_usd is None
    assert summary.unpriced_models == []


# --- §SD1: the envelope still wins where a harness supplies one --------------


def test_envelope_total_cost_overrides_token_derived(tmp_path, registry):
    claude_store.set_pricing(registry)
    path = _session(
        tmp_path,
        [_assistant("claude-opus-5", _usage(inp=1 * M), total_cost_usd=0.42)],
    )
    summary = claude_store.read_session(path)
    assert summary.total_cost_usd == pytest.approx(0.42)
    assert summary.cost_partial is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
