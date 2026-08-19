from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .hypercore import bracket_points, fill_coverage, portfolio_series
from .timeutils import iso_utc


def build_core_report(
    wallet: str,
    cutoff_ms: int,
    event_start_ms: int,
    event_end_ms: int,
    portfolio: Any,
    funding: Any,
    ledger: Any,
    fills: Any,
    current_perp: Any,
    current_spot: Any,
    subaccounts: Any,
    vaults: Any,
    staking_summary: Any,
    staking_history: Any,
) -> dict[str, Any]:
    points = portfolio_series(portfolio, "allTime")
    before, after = bracket_points(points, cutoff_ms)
    return {
        "wallet": wallet,
        "cutoff_ms": cutoff_ms,
        "cutoff_utc": iso_utc(cutoff_ms),
        "portfolio_snapshot": {
            "before": asdict(before) if before else None,
            "after": asdict(after) if after else None,
            "note": "Portfolio history is a sampled account-value series, not an exact block-state snapshot.",
        },
        "cutoff_window": {
            "start_ms": event_start_ms,
            "end_ms": event_end_ms,
            "funding": funding,
            "non_funding_ledger_updates": ledger,
            "fills": fills,
            "fill_coverage": fill_coverage(fills, event_start_ms, event_end_ms),
        },
        "current_state_for_reconciliation": {
            "perp": current_perp,
            "spot": current_spot,
            "subaccounts": subaccounts,
            "vault_equities": vaults,
            "staking_summary": staking_summary,
            "staking_history": staking_history,
        },
        "confidence": {
            "level": "diagnostic",
            "reason": (
                "This report combines sampled portfolio history and public API event data. Exact historical state requires complete event coverage or an archival state source."
            ),
        },
    }
