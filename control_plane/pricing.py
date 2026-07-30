"""Pricing registry loader + cache-aware token→USD calculator (ADR-0022).

Replaces the ADR-0014 shape (a flat `{model: {input, output}}` table and a
2-token `calculate_cost`). Three things changed, all measured against the real
session store rather than a fixture:

  1. **Token-derived cost is the primary path** (§SD1). No Claude Code assistant
     event carries an envelope `total_cost_usd`, so the "fallback" was in fact
     the only mechanism. The envelope read stays as an *override* for harnesses
     that do supply one — `claude_store` applies it before calling here.
  2. **Resolution is exact match plus declared aliases** (§SD2). No prefix or
     family inference: a model we cannot price returns `None` and is logged
     once, because a blank cell is cheaper than a plausible wrong number.
  3. **Four token classes, not two** (§SD3). Cache reads dominate real usage —
     `input_tokens` + `output_tokens` were 0.42% of billable input across the
     store. Cache rates are provider-wide *ratios* of the model's input rate,
     held once at the top of the registry and overridable per model.

Harness-specific parsing of a `usage` block does **not** live here — stores own
their own transcript shapes and hand a `TokenUsage` to `calculate_cost`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Optional

# Provider-wide defaults, used when the registry omits `cache_multipliers`.
# Ratios of the model's input rate: a 5-minute cache write bills at 1.25x
# input, a 1-hour write at 2x, and a cache read at 0.1x.
_DEFAULT_WRITE_5M = 1.25
_DEFAULT_WRITE_1H = 2.0
_DEFAULT_READ = 0.1

# `checked_on` is compared as a string by oldest_checked_on(), so the zero-padded
# ISO form is load-bearing, not cosmetic: it makes lexical order date order.
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class CacheMultipliers:
    """Cache rates as ratios of a model's input rate."""

    write_5m: float = _DEFAULT_WRITE_5M
    write_1h: float = _DEFAULT_WRITE_1H
    read: float = _DEFAULT_READ


@dataclass(frozen=True)
class ModelRate:
    """USD per 1M tokens for one model, plus its resolved cache ratios.

    `source` and `checked_on` are mandatory (ADR-0026 §SD2). A rate's
    correctness has no offline oracle; the presence of its provenance does, so
    that is what the loader enforces.
    """

    input: float
    output: float
    source: str
    checked_on: str
    cache: CacheMultipliers = CacheMultipliers()


@dataclass(frozen=True)
class TokenUsage:
    """Billable tokens for one model within one session (ADR-0022 §SD3)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    cache_read: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_write_5m=self.cache_write_5m + other.cache_write_5m,
            cache_write_1h=self.cache_write_1h + other.cache_write_1h,
            cache_read=self.cache_read + other.cache_read,
        )


@dataclass(frozen=True)
class PricingRegistry:
    """The committed rate card: models, plus the aliases that resolve into it."""

    models: dict[str, ModelRate]
    aliases: dict[str, str]
    cache_source: str = ""
    cache_checked_on: str = ""

    def oldest_checked_on(self, models: list[str]) -> Optional[str]:
        """The stalest `checked_on` among models that could be priced.

        §SD3 surfaces this next to a session's cost: with several models
        contributing, the figure is only as trustworthy as its least recently
        verified rate. Unpriceable ids contribute nothing — they are already
        reported separately as `unpriced_models`.
        """
        dates = [r.checked_on for r in (self.rate_for(m) for m in models) if r]
        return min(dates) if dates else None

    def rate_for(self, model: str) -> Optional[ModelRate]:
        """Exact match, then a declared alias. Never a prefix guess (§SD2)."""
        rate = self.models.get(model)
        if rate is None:
            key = self.aliases.get(model)
            if key is not None:
                rate = self.models.get(key)
        return rate

    def __len__(self) -> int:
        return len(self.models)


# --- Drift guard, runtime half (§SD2.3) --------------------------------------
#
# The test-time half is tests/test_transcript_contract.py, which fails when the
# real store reports an id the registry cannot price. This half covers the
# machines that never run the suite: the first time a session reports an
# unpriceable model, say so on stdout. Once per id per process — read_session()
# runs on every /state build, and a per-parse log would bury the signal it
# exists to raise.
_UNPRICED_SEEN: set[str] = set()


def reset_unpriced_log() -> None:
    """Clear the once-per-process log. For tests."""
    _UNPRICED_SEEN.clear()


def _note_unpriced(model: str) -> None:
    if model in _UNPRICED_SEEN:
        return
    _UNPRICED_SEEN.add(model)
    print(
        f"switchboard: no rate for model {model!r} — sessions using it render "
        f"as 'unpriced'. Add it to pricing.json (or alias it to an existing key)."
    )


# --- Registry loading --------------------------------------------------------


def _float_field(value: object, where: str) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"pricing.json {where} must be a number, got {value!r}")


def _multipliers(block: object, base: CacheMultipliers, where: str) -> CacheMultipliers:
    """Merge a partial `cache_multipliers` block over `base`.

    A model that follows the standard ratios needs no cache fields at all; one
    that deviates on a single ratio overrides only that one (§SD3, option A).
    """
    if block is None:
        return base
    if not isinstance(block, dict):
        raise ValueError(f"pricing.json {where} must be an object")
    merged = base
    for key in ("write_5m", "write_1h", "read"):
        if key in block:
            merged = replace(merged, **{key: _float_field(block[key], f"{where}.{key}")})
    return merged


def _provenance(block: dict, where: str) -> tuple[str, str]:
    """Validate and extract `source` + `checked_on` from a rate block (§SD2).

    Rejected rather than defaulted: optional means absent, and absent is the
    state that let a wrong opus rate sit uncontested from its first commit.
    """
    source = block.get("source")
    if not isinstance(source, str) or not source.startswith(("https://", "http://")):
        raise ValueError(
            f"pricing.json {where}.source must be a URL — a source you cannot "
            f"open is not provenance (ADR-0026 §SD2); got {source!r}"
        )
    checked_on = block.get("checked_on")
    if not isinstance(checked_on, str) or not _ISO_DATE.fullmatch(checked_on):
        raise ValueError(
            f"pricing.json {where}.checked_on must be an ISO date (YYYY-MM-DD); "
            f"got {checked_on!r}"
        )
    try:
        date.fromisoformat(checked_on)
    except ValueError as e:
        raise ValueError(f"pricing.json {where}.checked_on is not a real date") from e
    return source, checked_on


def load_pricing(path: str) -> PricingRegistry:
    """Read pricing.json and return the resolved registry.

    Validates at load time and raises so the server can start with cost
    disabled rather than silently pricing everything to None.

    Args:
        path: absolute path to pricing.json

    Returns:
        PricingRegistry with per-model rates and cache ratios already merged.

    Raises:
        FileNotFoundError: pricing.json missing
        ValueError: invalid schema
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("pricing.json root must be an object")

    # `default_fallback` is retained from ADR-0014 §SD2 and keeps its meaning:
    # unknown model -> None. Guessing a rate is deliberately not implemented,
    # so a file that asks for one must fail rather than be quietly ignored.
    if data.get("default_fallback") is not None:
        raise ValueError(
            "pricing.json default_fallback must be null — guessing a rate for an "
            "unknown model is not implemented (ADR-0022 §SD2)"
        )

    cache_block = data.get("cache_multipliers")
    base_cache = _multipliers(cache_block, CacheMultipliers(), "cache_multipliers")
    # Provenance is required for a *committed* block only. Omitting the block
    # entirely falls back to the module constants above, which are code with a
    # comment rather than a rate card entry — there is nothing to cite.
    if isinstance(cache_block, dict):
        cache_source, cache_checked_on = _provenance(cache_block, "cache_multipliers")
    else:
        cache_source, cache_checked_on = "", ""

    models_raw = data.get("models")
    if not isinstance(models_raw, dict):
        raise ValueError("pricing.json 'models' must be a dict")

    models: dict[str, ModelRate] = {}
    for model_id, prices in models_raw.items():
        where = f"models.{model_id}"
        if not isinstance(prices, dict):
            raise ValueError(f"pricing.json {where} must be an object")
        if prices.get("input") is None or prices.get("output") is None:
            raise ValueError(f"pricing.json {where} missing 'input' or 'output'")
        model_source, model_checked_on = _provenance(prices, where)
        models[model_id] = ModelRate(
            input=_float_field(prices["input"], f"{where}.input"),
            output=_float_field(prices["output"], f"{where}.output"),
            source=model_source,
            checked_on=model_checked_on,
            cache=_multipliers(
                prices.get("cache_multipliers"),
                base_cache,
                f"{where}.cache_multipliers",
            ),
        )

    aliases_raw = data.get("aliases") or {}
    if not isinstance(aliases_raw, dict):
        raise ValueError("pricing.json 'aliases' must be a dict")
    aliases: dict[str, str] = {}
    for reported_id, target in aliases_raw.items():
        if not isinstance(target, str) or target not in models:
            raise ValueError(
                f"pricing.json aliases.{reported_id} points at {target!r}, "
                f"which is not a key in 'models'"
            )
        aliases[reported_id] = target

    return PricingRegistry(
        models=models,
        aliases=aliases,
        cache_source=cache_source,
        cache_checked_on=cache_checked_on,
    )


# --- Cost --------------------------------------------------------------------


def calculate_cost(
    usage: TokenUsage,
    model: str,
    registry: Optional[PricingRegistry],
) -> Optional[float]:
    """USD for one model's usage, or None when the model has no rate.

    (input + write_5m*m.write_5m + write_1h*m.write_1h + read*m.read) * input_rate
      + output * output_rate,  all / 1_000_000

    Args:
        usage: billable tokens for this model
        model: model identifier as the transcript reports it
        registry: from load_pricing(), or None when cost is disabled

    Returns:
        cost in USD, or None when the model is unpriceable.
    """
    if registry is None:
        return None
    rate = registry.rate_for(model)
    if rate is None:
        _note_unpriced(model)
        return None

    input_units = (
        usage.input_tokens
        + usage.cache_write_5m * rate.cache.write_5m
        + usage.cache_write_1h * rate.cache.write_1h
        + usage.cache_read * rate.cache.read
    )
    return (input_units * rate.input + usage.output_tokens * rate.output) / 1_000_000
