#!/usr/bin/env python3
"""Pricing registry + cache-aware cost calculator (ADR-0022, replaces ADR-0014).

The ADR-0014 shape these tests used to cover — a flat `{model: {input, output}}`
table and a 2-token `calculate_cost(input, output, ...)` — priced 15 of 24 local
sessions to `None` and understated the rest ~2.2x, because Claude Code bills
almost entirely through the prompt cache. What is asserted here now:

  - the registry resolves a model exactly, or through a *declared* alias
  - cache multipliers are derived from the model's input rate, with an optional
    per-model override merged over the top-level block
  - cost sums four token classes, not two
  - an unknown model still returns None — never a guess (ADR-0022 §SD2)

Run:
    python3 -m pytest projects/switchboard/repos/switchboard/tests/test_pricing.py -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from control_plane import pricing  # noqa: E402
from control_plane.pricing import TokenUsage  # noqa: E402

# --- Fixtures ----------------------------------------------------------------


@pytest.fixture
def sample_pricing_json():
    return {
        "cache_multipliers": {"write_5m": 1.25, "write_1h": 2.0, "read": 0.1},
        "aliases": {"claude-opus-4-8-20260115": "claude-opus-4-8"},
        "models": {
            "claude-sonnet-5": {"input": 3.00, "output": 15.00},
            "claude-opus-4-8": {"input": 5.00, "output": 25.00},
            # Non-standard read ratio — exercises the per-model override merge.
            "cheap-reader": {
                "input": 10.00,
                "output": 50.00,
                "cache_multipliers": {"read": 0.05},
            },
        },
        "default_fallback": None,
    }


def _write(tmp_path: Path, payload: dict) -> str:
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


@pytest.fixture
def registry(sample_pricing_json, tmp_path):
    return pricing.load_pricing(_write(tmp_path, sample_pricing_json))


@pytest.fixture
def pricing_path(sample_pricing_json):
    fd, path = tempfile.mkstemp(suffix=".json", prefix="pricing-test-")
    with os.fdopen(fd, "w") as f:
        json.dump(sample_pricing_json, f)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


# --- Test: load_pricing ------------------------------------------------------


def test_load_pricing_valid(pricing_path):
    reg = pricing.load_pricing(pricing_path)
    rate = reg.rate_for("claude-sonnet-5")
    assert rate is not None
    assert rate.input == 3.00
    assert rate.output == 15.00


def test_load_pricing_file_not_found():
    with pytest.raises(FileNotFoundError):
        pricing.load_pricing("/nonexistent/pricing.json")


def test_load_pricing_missing_models_key(tmp_path):
    with pytest.raises(ValueError):
        pricing.load_pricing(_write(tmp_path, {"not_models": {}}))


def test_load_pricing_models_not_dict(tmp_path):
    with pytest.raises(ValueError):
        pricing.load_pricing(_write(tmp_path, {"models": "not-a-dict"}))


def test_load_pricing_invalid_price_type(tmp_path):
    with pytest.raises(ValueError):
        pricing.load_pricing(
            _write(tmp_path, {"models": {"m": {"input": "free", "output": 15.0}}})
        )


def test_load_pricing_missing_price_key(tmp_path):
    with pytest.raises(ValueError):
        pricing.load_pricing(_write(tmp_path, {"models": {"m": {"input": 3.0}}}))


def test_load_pricing_defaults_cache_multipliers_when_absent(tmp_path):
    """A registry with no cache block still prices cache tokens, not zero."""
    reg = pricing.load_pricing(
        _write(tmp_path, {"models": {"m": {"input": 10.0, "output": 50.0}}})
    )
    cache = reg.rate_for("m").cache
    assert (cache.write_5m, cache.write_1h, cache.read) == (1.25, 2.0, 0.1)


def test_load_pricing_per_model_override_merges_over_top_level(registry):
    """A partial override is legal — unstated ratios keep the top-level value."""
    cache = registry.rate_for("cheap-reader").cache
    assert cache.read == 0.05
    assert cache.write_5m == 1.25
    assert cache.write_1h == 2.0


def test_load_pricing_rejects_bad_multiplier_type(tmp_path):
    with pytest.raises(ValueError):
        pricing.load_pricing(
            _write(
                tmp_path,
                {
                    "cache_multipliers": {"read": "cheap"},
                    "models": {"m": {"input": 3.0, "output": 15.0}},
                },
            )
        )


def test_load_pricing_rejects_alias_to_unknown_model(tmp_path):
    """Aliases are declared and reviewable — a dangling one is a typo, not a miss."""
    with pytest.raises(ValueError):
        pricing.load_pricing(
            _write(
                tmp_path,
                {
                    "aliases": {"some-snapshot": "no-such-model"},
                    "models": {"m": {"input": 3.0, "output": 15.0}},
                },
            )
        )


def test_load_pricing_rejects_non_null_default_fallback(tmp_path):
    """ADR-0022 §SD3 keeps `default_fallback: null` — guessing is not implemented,
    so a file that asks for a guess must fail loudly rather than be ignored."""
    with pytest.raises(ValueError):
        pricing.load_pricing(
            _write(
                tmp_path,
                {
                    "models": {"m": {"input": 3.0, "output": 15.0}},
                    "default_fallback": {"input": 1.0, "output": 2.0},
                },
            )
        )


# --- Test: model resolution (ADR-0022 §SD2) ----------------------------------


def test_rate_for_exact_match(registry):
    assert registry.rate_for("claude-opus-4-8").input == 5.00


def test_rate_for_declared_alias(registry):
    aliased = registry.rate_for("claude-opus-4-8-20260115")
    assert aliased is not None
    assert aliased.input == 5.00


def test_rate_for_unknown_model_is_none(registry):
    """No prefix/family inference — a near-miss must not price as its neighbour."""
    assert registry.rate_for("claude-opus-4-8-turbo-experimental") is None
    assert registry.rate_for("deepseek-v4-pro") is None


# --- Test: calculate_cost (ADR-0022 §SD3) ------------------------------------


def test_calculate_cost_sums_four_token_classes(registry):
    usage = TokenUsage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_write_5m=1_000_000,
        cache_write_1h=1_000_000,
        cache_read=1_000_000,
    )
    cost = pricing.calculate_cost(usage, "claude-sonnet-5", registry)
    # input units = 1 + 1.25 + 2.0 + 0.1 = 4.35M -> * $3 = $13.05
    # output = 1M * $15 = $15.00
    assert cost == pytest.approx(28.05)


def test_calculate_cost_cache_read_dominates(registry):
    """The real shape: near-zero fresh input, a huge cache_read."""
    usage = TokenUsage(input_tokens=2, output_tokens=0, cache_read=10_000_000)
    cost = pricing.calculate_cost(usage, "claude-opus-4-8", registry)
    # 10M * 0.1 = 1M units * $5 = $5.00 (+ 2 tokens, negligible but counted)
    assert cost == pytest.approx(5.00001)


def test_calculate_cost_applies_per_model_override(registry):
    usage = TokenUsage(cache_read=1_000_000)
    cost = pricing.calculate_cost(usage, "cheap-reader", registry)
    # 1M * 0.05 = 50k units * $10 / 1M = $0.50
    assert cost == pytest.approx(0.50)


def test_calculate_cost_zero_usage(registry):
    assert pricing.calculate_cost(TokenUsage(), "claude-sonnet-5", registry) == 0.0


def test_calculate_cost_unknown_model(registry):
    usage = TokenUsage(input_tokens=1000)
    assert pricing.calculate_cost(usage, "nope", registry) is None


def test_calculate_cost_no_registry():
    assert pricing.calculate_cost(TokenUsage(input_tokens=1000), "any", None) is None


def test_calculate_cost_resolves_alias(registry):
    usage = TokenUsage(input_tokens=1_000_000)
    assert pricing.calculate_cost(
        usage, "claude-opus-4-8-20260115", registry
    ) == pytest.approx(5.00)


# --- Test: drift guard, runtime half (ADR-0022 §SD2.3) -----------------------


def test_unknown_model_is_reported_once(registry, capsys):
    pricing.reset_unpriced_log()
    for _ in range(3):
        pricing.calculate_cost(TokenUsage(input_tokens=10), "ghost-model", registry)
    out = capsys.readouterr().out
    assert out.count("ghost-model") == 1, (
        "the drift guard must log an unknown model id once per process, not per parse"
    )


def test_unpriced_log_separates_distinct_models(registry, capsys):
    pricing.reset_unpriced_log()
    pricing.calculate_cost(TokenUsage(), "ghost-a", registry)
    pricing.calculate_cost(TokenUsage(), "ghost-b", registry)
    out = capsys.readouterr().out
    assert "ghost-a" in out and "ghost-b" in out


# --- Run ---------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
