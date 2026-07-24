"""Pricing registry loader + token→USD calculator (ADR-0014).

Single public entry point at module level — load_pricing() reads the committed
pricing.json and returns a model→price table. calculate_cost() is a pure function
that converts input+output tokens to USD using the pricing table.

pricing.json is authoritative only as a FALLBACK: jsonl envelope total_cost_usd
takes precedence over computed cost.
"""

from __future__ import annotations

import json
from typing import Optional


def load_pricing(path: str) -> dict[str, dict[str, float]]:
    """Read pricing.json and return {model_id: {input: float, output: float}}.

    Validates schema at load time. Raises ValueError for invalid files so the
    server can start with cost fallback disabled.

    Args:
        path: absolute path to pricing.json

    Returns:
        dict of model_id → {input: price_per_1M, output: price_per_1M}

    Raises:
        FileNotFoundError: pricing.json missing
        ValueError: invalid schema
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("pricing.json root must be an object")
    models = data.get("models")
    if not isinstance(models, dict):
        raise ValueError("pricing.json 'models' must be a dict")

    parsed: dict[str, dict[str, float]] = {}
    for model_id, prices in models.items():
        if not isinstance(prices, dict):
            raise ValueError(f"pricing.json models.{model_id} must be an object")
        inp = prices.get("input")
        out = prices.get("output")
        if inp is None or out is None:
            raise ValueError(
                f"pricing.json models.{model_id} missing 'input' or 'output'"
            )
        try:
            inp_f = float(inp)
            out_f = float(out)
        except (TypeError, ValueError):
            raise ValueError(f"pricing.json models.{model_id} prices must be numbers")
        parsed[model_id] = {"input": inp_f, "output": out_f}

    return parsed


def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    model: str,
    pricing_table: Optional[dict[str, dict[str, float]]],
) -> Optional[float]:
    """Convert token counts to USD using the pricing table.

    Formula: (input_tokens * input_price + output_tokens * output_price) / 1_000_000

    Args:
        input_tokens: total input/prompt tokens
        output_tokens: total output/completion tokens
        model: model identifier (e.g. "claude-sonnet-5")
        pricing_table: pricing data from load_pricing()

    Returns:
        cost in USD, or None when model not in table or table is None
    """
    if pricing_table is None:
        return None
    prices = pricing_table.get(model)
    if prices is None:
        return None
    return (
        input_tokens * prices["input"] + output_tokens * prices["output"]
    ) / 1_000_000
