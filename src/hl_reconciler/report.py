from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from typing import Any

from .assets import (
    borrow_lend_interest_forward_deltas,
    combine_deltas,
    current_spot_balances,
    current_staking_hype,
    dec,
    fill_forward_deltas,
    funding_forward_deltas,
    ledger_forward_deltas,
    reconstruct_from_current,
    serialize_balances,
    spot_pair_map,
    staking_reward_forward_deltas,
)
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
            "reason": "This report combines sampled portfolio history and public API event data. Exact historical state requires complete event coverage or an archival state source.",
        },
    }


def _standard_perp_cash(current_perp: Any) -> Decimal | None:
    if not isinstance(current_perp, dict):
        return None
    summary = current_perp.get("marginSummary")
    if not isinstance(summary, dict) or summary.get("accountValue") is None:
        return None
    unrealized = Decimal("0")
    for wrapper in current_perp.get("assetPositions") or []:
        position = wrapper.get("position") if isinstance(wrapper, dict) else None
        if isinstance(position, dict):
            unrealized += dec(position.get("unrealizedPnl"))
    return dec(summary.get("accountValue")) - unrealized


def _first_post_cutoff_activity_ms(fills: Any, funding: Any, ledger: Any) -> int | None:
    times: list[int] = []
    for collection in (fills, funding, ledger):
        for row in collection if isinstance(collection, list) else []:
            if isinstance(row, dict) and row.get("time") is not None:
                try:
                    times.append(int(row["time"]))
                except (TypeError, ValueError):
                    pass
    return min(times) if times else None


def build_asset_statement(
    wallet: str,
    cutoff_ms: int,
    history_end_ms: int,
    current_spot: Any,
    current_perp: Any,
    spot_meta: Any,
    post_cutoff_fills: Any,
    post_cutoff_funding: Any,
    post_cutoff_ledger: Any,
    staking_summary: Any,
    staking_rewards: Any,
    borrow_lend_state: Any,
    borrow_lend_interest: Any,
    abstraction: Any,
    vaults: Any,
) -> dict[str, Any]:
    current_owned = current_spot_balances(current_spot)
    staked_hype = current_staking_hype(staking_summary)
    current_owned["HYPE"] = current_owned.get("HYPE", Decimal("0")) + staked_hype

    abstraction_name = str(abstraction) if abstraction is not None else "unknown"
    unified_cash = abstraction_name in {"unifiedAccount", "portfolioMargin"}
    separate_perp_cash = Decimal("0")
    perp_cash_available = True
    if not unified_cash:
        derived = _standard_perp_cash(current_perp)
        if derived is None:
            perp_cash_available = False
        else:
            separate_perp_cash = derived
            current_owned["USDC"] = current_owned.get("USDC", Decimal("0")) + derived

    pairs = spot_pair_map(spot_meta)
    fill_deltas, fill_warnings = fill_forward_deltas(post_cutoff_fills, pairs)
    funding_deltas = funding_forward_deltas(post_cutoff_funding)
    ledger_deltas, ledger_type_counts, ledger_warnings = ledger_forward_deltas(post_cutoff_ledger, wallet)
    staking_deltas = staking_reward_forward_deltas(staking_rewards, cutoff_ms)
    supply_interest_deltas, borrow_interest_deltas = borrow_lend_interest_forward_deltas(borrow_lend_interest, cutoff_ms)
    forward = combine_deltas(fill_deltas, funding_deltas, ledger_deltas, staking_deltas, supply_interest_deltas)
    historical = reconstruct_from_current(current_owned, forward)

    fills = post_cutoff_fills if isinstance(post_cutoff_fills, list) else []
    fill_times = [int(row["time"]) for row in fills if isinstance(row, dict) and row.get("time") is not None]
    earliest_fill = min(fill_times) if fill_times else None
    latest_fill = max(fill_times) if fill_times else None
    first_activity = _first_post_cutoff_activity_ms(post_cutoff_fills, post_cutoff_funding, post_cutoff_ledger)

    warnings = fill_warnings + ledger_warnings
    if not unified_cash and not perp_cash_available:
        warnings.append({"type": "missing_standard_perp_cash", "abstraction": abstraction_name})
    if len(fills) >= 10000:
        warnings.append({"type": "fill_history_limit_risk", "count": len(fills)})

    # A fill gap by itself is not evidence of incomplete history: an account can simply
    # be inactive. We only flag it when reversing the returned post-cutoff activity
    # produces a materially negative historical balance, which is direct evidence that
    # an earlier funding/activity event is missing from the public reconstruction.
    materially_negative = {
        token: amount for token, amount in historical.items() if amount < Decimal("-0.000001")
    }
    if materially_negative and first_activity is not None and first_activity > cutoff_ms + 24 * 60 * 60 * 1000:
        warnings.append({
            "type": "history_gap_with_negative_balance",
            "first_post_cutoff_activity_ms": first_activity,
            "negative_balances": serialize_balances(materially_negative, dust=Decimal("0")),
            "note": "Returned history begins well after the cutoff and backward reconstruction becomes negative. Inspect the first funding/transfer before using this account in the consolidated total.",
        })

    severe_warning_types = {
        "unknown_spot_pair", "unsupported_ledger_type", "fill_history_limit_risk",
        "history_gap_with_negative_balance", "missing_standard_perp_cash", "borrow_lend_liability_flow",
    }
    severe = [warning for warning in warnings if warning.get("type") in severe_warning_types]
    confidence = "high" if not severe and not materially_negative else "review_required"

    return {
        "scope": {
            "account": wallet,
            "includes": ["HyperCore spot token balances", "separate Standard-mode perp cash when applicable", "HYPE held in staking", "realized perp PnL and trading fees", "funding payments", "deposits, withdrawals and transfers", "staking rewards", "borrow/lend supply interest"],
            "excludes_from_primary_balance": ["open perp notional", "unrealized perp PnL", "historical vault equity valuation", "HyperEVM assets (reported separately)"],
        },
        "cutoff_ms": cutoff_ms,
        "cutoff_utc": iso_utc(cutoff_ms),
        "history_end_ms": history_end_ms,
        "history_end_utc": iso_utc(history_end_ms),
        "current_abstraction": abstraction_name,
        "current_owned_balances": serialize_balances(current_owned),
        "current_staking_hype": format(staked_hype, "f"),
        "current_separate_perp_cash_usdc": format(separate_perp_cash, "f"),
        "post_cutoff_forward_deltas": serialize_balances(forward, dust=Decimal("0")),
        "historical_balances_at_cutoff": serialize_balances(historical),
        "activity_summary": {
            "fills": len(fills),
            "funding_events": len(post_cutoff_funding) if isinstance(post_cutoff_funding, list) else 0,
            "ledger_events": len(post_cutoff_ledger) if isinstance(post_cutoff_ledger, list) else 0,
            "ledger_type_counts": ledger_type_counts,
            "earliest_fill_ms": earliest_fill,
            "latest_fill_ms": latest_fill,
            "first_post_cutoff_activity_ms": first_activity,
        },
        "borrow_lend": {"current_state": borrow_lend_state, "post_cutoff_borrow_interest_by_token": serialize_balances(borrow_interest_deltas, dust=Decimal("0")), "note": "Borrow principal/liabilities are shown separately from owned asset quantities."},
        "vaults": {"current_equities": vaults, "note": "Any vault-flow warning means historical vault equity still needs separate valuation before a total-wallet figure is final."},
        "warnings": warnings,
        "confidence": {"level": confidence, "reason": "High means reversing the available post-cutoff event stream produces non-negative balances with no unsupported material event types. Archive replay remains the audit fallback when a history gap is evidenced."},
    }
