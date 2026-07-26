#!/usr/bin/env python3
"""Contract tests — the backend's assumptions vs the transcripts it actually reads.

Every other backend test builds its own fixture, so it can only prove that a
module agrees with itself. Both v2.6 branches shipped green that way and were
inert in production: health.py parsed a message envelope no harness emits, and
pricing.json was keyed on model ids no session reports.

These tests read the *real* session store instead. They skip when the store is
absent (CI runners, fresh checkouts), so they cost nothing there — the point is
that a local run, or any runner with a session store mounted, fails loudly when
an external schema drifts out from under us.

Run:
    python3 -m pytest projects/switchboard/repos/switchboard/tests/test_transcript_contract.py -v
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from control_plane import health, pricing  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

# A transcript needs some substance before it can prove anything about parsing.
_MIN_LINES = 20

# Deliberately NOT config.SESSION_ROOT: test_agy_harness / test_codex_harness /
# test_provider / test_perf_caching_stores each set ORCH_SESSION_ROOT to a tmp
# dir at import time, so by the time the full suite reaches this module the
# shared config points at a fixture tree with nothing real in it. These tests
# exist to check the store on disk, so they resolve it themselves and take an
# override of their own.
SESSION_ROOT = Path(
    os.environ.get(
        "SWITCHBOARD_CONTRACT_SESSION_ROOT",
        os.path.expanduser("~/.claude/projects"),
    )
)


# --- Fixtures ---------------------------------------------------------------


def _claude_transcripts() -> list[Path]:
    """Real claude jsonl transcripts, largest first (most signal per file)."""
    root = SESSION_ROOT
    if not root.is_dir():
        return []
    files = [p for p in root.glob("*/*.jsonl") if p.stat().st_size > 0]
    return sorted(files, key=lambda p: p.stat().st_size, reverse=True)


@pytest.fixture(scope="module")
def transcripts() -> list[Path]:
    files = _claude_transcripts()
    if not files:
        pytest.skip(f"no claude session store at {SESSION_ROOT}")
    return files


@pytest.fixture(scope="module")
def biggest_transcript(transcripts: list[Path]) -> Path:
    if transcripts[0].stat().st_size < 1024:
        pytest.skip("session store has no transcript with enough turns to score")
    return transcripts[0]


def _events(path: Path) -> Iterator[dict]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict):
                yield ev


# --- Envelope shape ---------------------------------------------------------


def test_claude_events_nest_the_message_envelope(transcripts):
    """role/content/stop_reason live under `message`, not at the top level.

    This is the assumption health.py got wrong: it filtered on a top-level
    `role`, which matches zero events, so loop and error scored healthy for
    every session regardless of what the session did.
    """
    checked = 0
    for path in transcripts:
        for ev in _events(path):
            if ev.get("type") not in ("user", "assistant"):
                continue
            checked += 1
            assert "role" not in ev, (
                f"{path.name}: event carries a top-level 'role' — the envelope "
                f"changed; re-check every consumer that dispatches on ev['type']"
            )
            assert isinstance(ev.get("message"), dict), (
                f"{path.name}: {ev.get('type')} event has no 'message' object"
            )

    if checked < _MIN_LINES:
        pytest.skip(f"only {checked} turn events in the store — too few to assert on")


def test_assistant_messages_carry_usage_and_model(transcripts):
    """Cost extraction depends on message.usage + message.model being present."""
    seen = 0
    for path in transcripts:
        for ev in _events(path):
            if ev.get("type") != "assistant":
                continue
            msg = ev.get("message") or {}
            if not isinstance(msg, dict) or msg.get("model") == "<synthetic>":
                continue
            seen += 1
            assert isinstance(msg.get("usage"), dict), (
                f"{path.name}: assistant message has no usage block — token "
                f"fallback pricing cannot work"
            )
            assert isinstance(msg.get("model"), str) and msg["model"], (
                f"{path.name}: assistant message has no model id"
            )

    if seen < _MIN_LINES:
        pytest.skip(f"only {seen} assistant events in the store — too few to assert on")


# --- Health actually sees the turns -----------------------------------------


def test_health_scores_real_assistant_turns(biggest_transcript):
    """session_health() must observe assistant turns in a real transcript.

    `error_total` counts the assistant turns the error window looked at. It was
    0 for every session in production — the regression this guards.
    """
    score = health.session_health(
        str(biggest_transcript), last_ts=None, harness="claude"
    )
    assert score is not None, "the largest real transcript scored as unreadable"
    assert score.error_total > 0, (
        "health saw 0 assistant turns in a real transcript — the message "
        "envelope drifted, or health stopped using the store reader"
    )


def test_health_unknown_harness_still_scores_stale(biggest_transcript):
    """An unrecognised harness degrades to stale-only, never to no health."""
    score = health.session_health(
        str(biggest_transcript), last_ts=None, harness="not-a-harness"
    )
    assert score is not None
    assert score.status == "healthy"
    assert score.error_total == 0


# --- Pricing registry coverage ----------------------------------------------


def _observed_models(transcripts: list[Path]) -> set[str]:
    models: set[str] = set()
    for path in transcripts:
        for ev in _events(path):
            if ev.get("type") != "assistant" or ev.get("isSidechain"):
                continue
            msg = ev.get("message") or {}
            if not isinstance(msg, dict):
                continue
            model = msg.get("model")
            # '<synthetic>' marks a locally-generated turn — never billed.
            if isinstance(model, str) and model and model != "<synthetic>":
                models.add(model)
    return models


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ADR-0014 model-id drift: pricing.json is keyed on ids no local session "
        "reports, so cost is None for most cards. Fix is the ADR-0014 delta "
        "(family matching + drift guard); delete this marker when it lands."
    ),
)
def test_pricing_registry_covers_observed_models(transcripts):
    """Every model id a real session reports must be priceable.

    `calculate_cost()` looks the id up exactly, so an id missing here is a
    silently blank cost on the board, not an error.
    """
    table = pricing.load_pricing(str(REPO_ROOT / "pricing.json"))
    observed = _observed_models(transcripts)
    if not observed:
        pytest.skip("no model ids in the session store")

    missing = sorted(observed - set(table))
    assert not missing, (
        f"pricing.json prices {len(table)} models but cannot price "
        f"{len(missing)} of {len(observed)} in use: {missing}"
    )


def test_pricing_registry_is_loadable():
    """The committed registry must parse — a broken file disables cost silently."""
    table = pricing.load_pricing(str(REPO_ROOT / "pricing.json"))
    assert table, "pricing.json parsed to an empty table"
    for model_id, prices in table.items():
        assert prices["input"] >= 0 and prices["output"] >= 0, (
            f"pricing.json {model_id} has a negative price"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
