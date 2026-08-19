from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from .hypercore import HyperCoreClient
from .hyperevm import EvmRpcClient, RpcError, find_block_at_or_before, wei_to_hype
from .report import build_asset_statement, build_core_report
from .timeutils import parse_local_cutoff, to_millis


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hl-balance",
        description="Historical Hyperliquid asset-balance reconciler",
    )
    p.add_argument("wallet")
    p.add_argument(
        "--at",
        required=True,
        help="Local or UTC ISO datetime, e.g. 2026-06-01T00:00:00",
    )
    p.add_argument("--timezone", default="America/Regina")
    p.add_argument(
        "--window-hours",
        type=int,
        default=24,
        help="Collect Core diagnostic events +/- this many hours around cutoff",
    )
    p.add_argument("--info-url", default="https://api.hyperliquid.xyz/info")
    p.add_argument(
        "--evm-rpc",
        help="Archive-capable HyperEVM RPC URL. Default Hyperliquid RPC cannot query historical state.",
    )
    p.add_argument(
        "--erc20",
        action="append",
        default=[],
        help="ERC-20 token contract to query at the historical EVM block; repeatable",
    )
    p.add_argument("--output", default="historical_balance.json")
    return p


def _safe(label: str, fn: Callable[[], Any], fallback: Any, errors: list[dict[str, str]]) -> Any:
    try:
        return fn()
    except Exception as exc:  # Keep optional enrichment from blocking the core statement.
        errors.append({"source": label, "error": str(exc)})
        return fallback


def _reconstruct_account(
    core: HyperCoreClient,
    address: str,
    cutoff_ms: int,
    history_end_ms: int,
    spot_meta: Any,
    current_spot: Any,
    current_perp: Any,
    staking_summary: Any | None = None,
    vaults: Any | None = None,
) -> dict[str, Any]:
    history_start_ms = cutoff_ms + 1
    errors: list[dict[str, str]] = []
    staking_summary = staking_summary if staking_summary is not None else _safe(
        "delegatorSummary", lambda: core.staking_summary(address), {}, errors
    )
    vaults = vaults if vaults is not None else _safe(
        "userVaultEquities", lambda: core.vault_equities(address), [], errors
    )

    statement = build_asset_statement(
        wallet=address,
        cutoff_ms=cutoff_ms,
        history_end_ms=history_end_ms,
        current_spot=current_spot,
        current_perp=current_perp,
        spot_meta=spot_meta,
        post_cutoff_fills=core.fills_range(address, history_start_ms, history_end_ms),
        post_cutoff_funding=core.funding_range(address, history_start_ms, history_end_ms),
        post_cutoff_ledger=core.ledger_range(address, history_start_ms, history_end_ms),
        staking_summary=staking_summary,
        staking_rewards=_safe("delegatorRewards", lambda: core.staking_rewards(address), [], errors),
        borrow_lend_state=_safe("borrowLendUserState", lambda: core.borrow_lend_state(address), {}, errors),
        borrow_lend_interest=_safe(
            "userBorrowLendInterest", lambda: core.borrow_lend_interest(address), [], errors
        ),
        abstraction=_safe("userAbstraction", lambda: core.user_abstraction(address), None, errors),
        vaults=vaults,
    )
    statement["optional_source_errors"] = errors
    return statement


def _aggregate_statements(master: dict[str, Any], subaccounts: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    accounts: list[dict[str, Any]] = [
        {
            "name": "Master",
            "address": master.get("scope", {}).get("account"),
            "statement": master,
        },
        *subaccounts,
    ]
    account_summaries: list[dict[str, Any]] = []
    all_high = True

    for account in accounts:
        statement = account["statement"]
        balances = statement.get("historical_balances_at_cutoff") or {}
        for token, amount in balances.items():
            totals[token] += Decimal(str(amount))
        level = statement.get("confidence", {}).get("level")
        all_high = all_high and level == "high"
        account_summaries.append(
            {
                "name": account.get("name"),
                "address": account.get("address"),
                "confidence": level,
                "historical_balances_at_cutoff": balances,
                "warnings": statement.get("warnings") or [],
            }
        )

    combined = {
        token: format(amount, "f")
        for token, amount in sorted(totals.items())
        if abs(amount) >= Decimal("0.00000001")
    }
    return {
        "historical_balances_at_cutoff": combined,
        "accounts": account_summaries,
        "confidence": {
            "level": "high" if all_high else "review_required",
            "reason": "Combined confidence is high only when the master account and every currently linked subaccount reconcile without material warnings.",
        },
        "note": "Internal transfers among included accounts should cancel when their sender and recipient histories are both available.",
    }


def run(args: argparse.Namespace) -> dict:
    cutoff = parse_local_cutoff(args.at, args.timezone)
    cutoff_ms = to_millis(cutoff)
    radius_ms = max(args.window_hours, 0) * 60 * 60 * 1000
    start_ms, end_ms = cutoff_ms - radius_ms, cutoff_ms + radius_ms

    core = HyperCoreClient(args.info_url)
    current_perp = core.clearinghouse_state(args.wallet)
    current_spot = core.spot_state(args.wallet)
    subaccounts = core.subaccounts(args.wallet)
    vaults = core.vault_equities(args.wallet)
    staking_summary = core.staking_summary(args.wallet)
    staking_history = core.staking_history(args.wallet)

    report = build_core_report(
        wallet=args.wallet,
        cutoff_ms=cutoff_ms,
        event_start_ms=start_ms,
        event_end_ms=end_ms,
        portfolio=core.portfolio(args.wallet),
        funding=core.funding(args.wallet, start_ms, end_ms),
        ledger=core.ledger(args.wallet, start_ms, end_ms),
        fills=core.fills(args.wallet, start_ms, end_ms),
        current_perp=current_perp,
        current_spot=current_spot,
        subaccounts=subaccounts,
        vaults=vaults,
        staking_summary=staking_summary,
        staking_history=staking_history,
    )

    # The year-end asset statement works backwards from today's actual balances and
    # reverses every supported balance-changing event after the requested cutoff.
    history_end_ms = int(time.time() * 1000)
    spot_meta = core.spot_meta()
    master_statement = _reconstruct_account(
        core=core,
        address=args.wallet,
        cutoff_ms=cutoff_ms,
        history_end_ms=history_end_ms,
        spot_meta=spot_meta,
        current_spot=current_spot,
        current_perp=current_perp,
        staking_summary=staking_summary,
        vaults=vaults,
    )
    report["asset_statement"] = master_statement

    subaccount_statements: list[dict[str, Any]] = []
    for sub in subaccounts if isinstance(subaccounts, list) else []:
        if not isinstance(sub, dict) or not sub.get("subAccountUser"):
            continue
        address = str(sub["subAccountUser"])
        statement = _reconstruct_account(
            core=core,
            address=address,
            cutoff_ms=cutoff_ms,
            history_end_ms=history_end_ms,
            spot_meta=spot_meta,
            current_spot=sub.get("spotState") or core.spot_state(address),
            current_perp=sub.get("clearinghouseState") or core.clearinghouse_state(address),
        )
        subaccount_statements.append(
            {
                "name": str(sub.get("name") or "Subaccount"),
                "address": address,
                "statement": statement,
            }
        )

    report["subaccount_asset_statements"] = subaccount_statements
    report["combined_hypercore_asset_statement"] = _aggregate_statements(
        master_statement, subaccount_statements
    )

    report["hyperevm"] = {
        "status": "not_requested",
        "note": "Pass --evm-rpc with an archive-capable RPC to query historical HyperEVM state.",
    }
    if args.evm_rpc:
        evm = EvmRpcClient(args.evm_rpc)
        cutoff_s = cutoff_ms // 1000
        try:
            located = find_block_at_or_before(evm, cutoff_s)
            native = evm.native_balance(args.wallet, located.number)
            tokens = []
            for token in args.erc20:
                tokens.append(
                    {
                        "token": token,
                        "raw_balance": str(
                            evm.erc20_balance(token, args.wallet, located.number)
                        ),
                    }
                )
            report["hyperevm"] = {
                "status": "ok",
                "block_number": located.number,
                "block_timestamp_s": located.timestamp_s,
                "native_hype_wei": str(native),
                "native_hype": wei_to_hype(native),
                "erc20_balances": tokens,
                "note": "ERC-20 values are raw token units; decimals/price enrichment is intentionally separate.",
            }
        except (RpcError, ValueError) as exc:
            report["hyperevm"] = {
                "status": "error",
                "error": str(exc),
                "note": "The default Hyperliquid RPC rejects historical-state calls; use an independent archive RPC.",
            }
    return report


def main() -> None:
    args = _parser().parse_args()
    report = run(args)
    output = Path(args.output)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output)


if __name__ == "__main__":
    main()
