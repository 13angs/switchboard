#!/usr/bin/env python3
"""P0#1 pricing — load pricing.json, calculate cost, schema validation.

Run:
    python3 projects/switchboard/repos/switchboard/tests/test_pricing.py
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

# --- Fixtures ----------------------------------------------------------------


@pytest.fixture
def sample_pricing_json():
    return {
        "models": {
            "claude-sonnet-5": {"input": 3.00, "output": 15.00},
            "claude-opus-4-8": {"input": 15.00, "output": 75.00},
            "gpt-5": {"input": 2.50, "output": 10.00},
        },
        "default_fallback": None,
    }


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
    table = pricing.load_pricing(pricing_path)
    assert isinstance(table, dict)
    assert "claude-sonnet-5" in table
    assert table["claude-sonnet-5"]["input"] == 3.00
    assert table["claude-sonnet-5"]["output"] == 15.00


def test_load_pricing_file_not_found():
    with pytest.raises(FileNotFoundError):
        pricing.load_pricing("/nonexistent/pricing.json")


def test_load_pricing_missing_models_key(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"not_models": {}}))
    with pytest.raises(ValueError):
        pricing.load_pricing(str(bad))


def test_load_pricing_models_not_dict(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"models": "not-a-dict"}))
    with pytest.raises(ValueError):
        pricing.load_pricing(str(bad))


def test_load_pricing_invalid_price_type(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "models": {"claude-sonnet-5": {"input": "free", "output": 15.00}},
                "default_fallback": None,
            }
        )
    )
    with pytest.raises(ValueError):
        pricing.load_pricing(str(bad))


def test_load_pricing_missing_price_key(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {"models": {"claude-sonnet-5": {"input": 3.00}}, "default_fallback": None}
        )
    )
    with pytest.raises(ValueError):
        pricing.load_pricing(str(bad))


# --- Test: calculate_cost ----------------------------------------------------


def test_calculate_cost_known_model(sample_pricing_json):
    cost = pricing.calculate_cost(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        model="claude-sonnet-5",
        pricing_table=sample_pricing_json["models"],
    )
    # (1M * 3.00 + 1M * 15.00) / 1M = 18.00
    assert cost == 18.00


def test_calculate_cost_zero_tokens(sample_pricing_json):
    cost = pricing.calculate_cost(
        input_tokens=0,
        output_tokens=0,
        model="claude-sonnet-5",
        pricing_table=sample_pricing_json["models"],
    )
    assert cost == 0.0


def test_calculate_cost_fractional(sample_pricing_json):
    cost = pricing.calculate_cost(
        input_tokens=500,
        output_tokens=250,
        model="gpt-5",
        pricing_table=sample_pricing_json["models"],
    )
    # (500 * 2.50 + 250 * 10.00) / 1M = (1250 + 2500) / 1M = 3750 / 1M = 0.00375
    assert cost == pytest.approx(0.00375)


def test_calculate_cost_unknown_model(sample_pricing_json):
    cost = pricing.calculate_cost(
        input_tokens=1000,
        output_tokens=1000,
        model="unknown-model",
        pricing_table=sample_pricing_json["models"],
    )
    assert cost is None


def test_calculate_cost_no_table():
    cost = pricing.calculate_cost(
        input_tokens=1000,
        output_tokens=1000,
        model="claude-sonnet-5",
        pricing_table=None,
    )
    assert cost is None


# --- Run ---------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
