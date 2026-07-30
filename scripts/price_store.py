#!/usr/bin/env python3
"""What the local claude session store cost, per session and in total.

Committed because ADR-0022's figures were not. Those were produced by a
design-time script that omitted `cache_read` — the single token class the ADR
existed to add, ~99.6% of the billable input it was arguing about — and because
the script was never in the tree, nobody could re-run it. The numbers reached
merge unchallenged and were wrong by ~2x. ADR-0024 §SD2 commits the measurement
so any cost figure in a design doc is one command from being re-checked.

**This script does not calculate cost.** It reports what
`claude_store.read_session()` already computed, which is the same code path the
board renders from. That is the rule, not an implementation detail: a reporting
script that re-derives cost is a second implementation that can be wrong on its
own, which is precisely the defect above rebuilt one directory over.
`tests/test_price_store.py` enforces it against a cache-heavy fixture.

Scope is the claude store (`~/.claude/projects`). codex keeps its own root and
agy reports no usage at all (ADR-0014, format limitation), so neither would add
a priced session here.

Usage:
    python3 scripts/price_store.py                  # ~/.claude/projects
    python3 scripts/price_store.py --store PATH     # a specific store root
    python3 scripts/price_store.py --pricing PATH   # a specific rate card
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from control_plane import claude_store, pricing  # noqa: E402

DEFAULT_STORE = Path(os.path.expanduser("~/.claude/projects"))
DEFAULT_PRICING = ROOT / "pricing.json"


@dataclass(frozen=True)
class SessionCost:
    session_id: str
    path: Path
    cost: Optional[float]  # None = nothing in this session could be priced
    partial: bool  # a priced subtotal — some model had no rate
    unpriced_models: list[str]

    @property
    def has_usage(self) -> bool:
        return self.cost is not None or bool(self.unpriced_models)


@dataclass(frozen=True)
class StoreReport:
    sessions: list[SessionCost]
    total: float
    unpriced_models: list[str] = field(default_factory=list)
    priced_count: int = 0
    unpriced_count: int = 0
    no_usage_count: int = 0
    store_root: Optional[Path] = None
    # Oldest `checked_on` across the rate card (ADR-0026 §SD3). The total is
    # only as trustworthy as its stalest rate, so the figure carries the date.
    rates_checked_on: Optional[str] = None


def price_store(
    store_root: Path = DEFAULT_STORE,
    pricing_path: Path = DEFAULT_PRICING,
) -> StoreReport:
    """Price every transcript under `store_root`, newest-cost first.

    Raises FileNotFoundError / ValueError when the rate card is missing or
    invalid. Reporting a total with cost silently disabled would be a $0.00
    that means "no rates loaded" — exactly the conflation ADR-0022 §SD2 exists
    to prevent.
    """
    registry = pricing.load_pricing(str(pricing_path))
    claude_store.set_pricing(registry)
    # read_session() is mtime-cached; a stale entry parsed under a different
    # rate card would be reported as if it were priced under this one.
    claude_store.invalidate_summary_cache()

    sessions: list[SessionCost] = []
    unpriced: set[str] = set()

    store_root = Path(store_root)
    if store_root.is_dir():
        for jsonl_path in sorted(store_root.glob("*/*.jsonl")):
            if jsonl_path.stat().st_size == 0:
                continue
            summary = claude_store.read_session(jsonl_path)
            models = sorted(summary.unpriced_models)
            unpriced.update(models)
            sessions.append(
                SessionCost(
                    session_id=summary.session_id or jsonl_path.stem,
                    path=jsonl_path,
                    cost=summary.total_cost_usd,
                    partial=summary.cost_partial,
                    unpriced_models=models,
                )
            )

    # Priced first, descending; then unpriced; then the ones with no usage data.
    sessions.sort(
        key=lambda s: (s.cost is None, -(s.cost or 0.0), not s.has_usage, s.session_id)
    )

    return StoreReport(
        sessions=sessions,
        total=sum(s.cost for s in sessions if s.cost is not None),
        unpriced_models=sorted(unpriced),
        priced_count=sum(1 for s in sessions if s.cost is not None),
        unpriced_count=sum(1 for s in sessions if s.cost is None and s.unpriced_models),
        no_usage_count=sum(1 for s in sessions if not s.has_usage),
        store_root=store_root,
        rates_checked_on=min(
            (r.checked_on for r in registry.models.values()), default=None
        ),
    )


def render(report: StoreReport) -> str:
    """The report as text. `unpriced` never renders as a dollar amount."""
    lines = [f"{'session':16} {'cost':>12}  note", f"{'-' * 16} {'-' * 12}  {'-' * 24}"]

    for s in report.sessions:
        if s.cost is None and s.unpriced_models:
            cost, note = "unpriced", "no rate: " + ", ".join(s.unpriced_models)
        elif s.cost is None:
            cost, note = "—", "no usage data"
        elif s.partial:
            cost = f"${s.cost:,.2f}+"
            note = "subtotal; no rate: " + ", ".join(s.unpriced_models)
        else:
            cost, note = f"${s.cost:,.2f}", ""
        lines.append(f"{s.session_id[:16]:16} {cost:>12}  {note}")

    lines += [
        "",
        f"{'TOTAL':16} {f'${report.total:,.2f}':>12}  "
        f"{report.priced_count} priced · {report.unpriced_count} unpriced · "
        f"{report.no_usage_count} no usage",
    ]
    if report.unpriced_models:
        lines.append(f"{'':16} {'':>12}  unpriced models: " + ", ".join(report.unpriced_models))
    # ADR-0024 §SD2 — a figure travels with what produced it. ADR-0026 §SD3
    # adds when the rates behind it were last verified against their sources.
    checked = (
        f" · rates checked {report.rates_checked_on}" if report.rates_checked_on else ""
    )
    lines.append(f"\nscripts/price_store.py · store {report.store_root}{checked}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--store", type=Path, default=DEFAULT_STORE)
    ap.add_argument("--pricing", type=Path, default=DEFAULT_PRICING)
    args = ap.parse_args(argv)

    try:
        report = price_store(args.store, args.pricing)
    except (FileNotFoundError, ValueError) as e:
        print(f"price_store: cannot price — {e}", file=sys.stderr)
        return 1

    print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
