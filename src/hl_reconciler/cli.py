from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from .evm_explorer import EtherscanCompatibleExplorer, replay_erc20_balances, replay_native_hype
from .hypercore import HyperCoreClient
from .hyperevm import EvmRpcClient, find_block_at_or_before, wei_to_hype
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
    p.add_argument(
        "--evm-explorer-key",
        default=os.environ.get("BLOCKSCOUT_API_KEY"),
        help=(
            "API key for an Etherscan/Blockscout-compatible HyperEVM explorer. "
            "Defaults to the BLOCKSCOUT_API_KEY environment variable."
        ),
    )
    p.add_argument(
        "--evm-explorer-url",
        default="https://api.blockscout.com/v2/api",
        help="Explorer API base URL. Default is Blockscout multichain API.",
    )
    p.add_argument(
        "--evm-explorer-chain-param",
        default="chain_id",
        choices=["chain_id", "chainid"],
        help="Explorer chain-ID query parameter (Blockscout=chain_id, Etherscan=chainid).",
    )
    p.add_argument("--output", default="historical_balance.json")
    return p


def _safe(label: str, fn: Callable[[], Any], fallback: Any, errors: list[dict[str, str]]) -> Any:
    try:
        return fn()
    except Exception as exc:
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
        {"name": "Master", "address": master.get("scope", {}).get("account"), "statement": master},
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


def _explorer_hyperevm(args: argparse.Namespace, cutoff_ms: int) -> dict[str, Any]:
    explorer = EtherscanCompatibleExplorer(
        base_url=args.evm_explorer_url,
        api_key=args.evm_explorer_key,
        chain_id=999,
        chain_param=args.evm_explorer_chain_param,
    )
    cutoff_s = cutoff_ms // 1000
    block = explorer.block_by_timestamp(cutoff_s, closest="before")
    transfers = explorer.token_transfers(args.wallet, 0, block)
    normal = explorer.normal_transactions(args.wallet, 0, block)
    internal = explorer.internal_transactions(args.wallet, 0, block)
    token_balances = replay_erc20_balances(args.wallet, transfers)
    native_hype = replay_native_hype(args.wallet, normal, internal)
    nonzero_tokens = [
        value for value in token_balances.values() if Decimal(str(value["balance"])) != 0
    ]
    return {
        "status": "ok",
        "method": "explorer_event_replay",
        "block_number": block,
        "cutoff_timestamp_s": cutoff_s,
        "native_hype_wei": str(int(native_hype * Decimal(10**18))),
        "native_hype": format(native_hype, "f"),
        "erc20_balances": nonzero_tokens,
        "event_counts": {
            "erc20_transfers": len(transfers),
            "normal_transactions": len(normal),
            "internal_transactions": len(internal),
        },
        "confidence": {
            "level": "high_for_erc20_review_native",
            "note": "ERC-20 balances are deterministic from complete Transfer-event history. Native HYPE replay should be cross-checked against archive state because an explorer may omit chain-specific/system value flows.",
        },
    }


def _archive_hyperevm(
    rpc_url: str,
    wallet: str,
    cutoff_ms: int,
    token_contracts: list[str],
) -> dict[str, Any]:
    evm = EvmRpcClient(rpc_url)
    located = find_block_at_or_before(evm, cutoff_ms // 1000)
    native = evm.native_balance(wallet, located.number)
    tokens: list[dict[str, Any]] = []
    token_errors = 0
    for token in dict.fromkeys(contract.lower() for contract in token_contracts):
        try:
            tokens.append(
                {
                    "token": token,
                    "raw_balance": str(evm.erc20_balance(token, wallet, located.number)),
                }
            )
        except Exception as exc:
            token_errors += 1
            tokens.append({"token": token, "status": "error", "error": str(exc)})
    return {
        "status": "partial" if token_errors else "ok",
        "method": "archive_rpc",
        "block_number": located.number,
        "block_timestamp_s": located.timestamp_s,
        "native_hype_wei": str(native),
        "native_hype": wei_to_hype(native),
        "erc20_balances": tokens,
        "token_query_errors": token_errors,
    }


def _compare_hyperevm_results(
    explorer_result: dict[str, Any],
    archive_result: dict[str, Any],
) -> dict[str, Any]:
    if archive_result.get("status") not in {"ok", "partial"}:
        return {
            "status": "unavailable",
            "note": "Archive RPC cross-check did not complete.",
        }

    replay_tokens = {
        str(row.get("contract") or "").lower(): str(row.get("raw_balance"))
        for row in explorer_result.get("erc20_balances") or []
        if isinstance(row, dict) and row.get("contract")
    }
    archive_tokens = {
        str(row.get("token") or "").lower(): str(row.get("raw_balance"))
        for row in archive_result.get("erc20_balances") or []
        if isinstance(row, dict) and row.get("token")
    }
    token_checks = [
        {
            "contract": contract,
            "explorer_raw_balance": replay_tokens.get(contract, "0"),
            "archive_raw_balance": archive_tokens.get(contract),
            "matches": archive_tokens.get(contract) == replay_tokens.get(contract, "0"),
        }
        for contract in sorted(replay_tokens.keys() | archive_tokens.keys())
    ]
    block_matches = explorer_result.get("block_number") == archive_result.get("block_number")
    native_matches = explorer_result.get("native_hype_wei") == archive_result.get(
        "native_hype_wei"
    )
    tokens_match = all(check["matches"] for check in token_checks)
    matched = block_matches and native_matches and tokens_match
    return {
        "status": "matched" if matched else "mismatch",
        "block_matches": block_matches,
        "native_hype_matches": native_matches,
        "erc20_balances_match": tokens_match,
        "tokens": token_checks,
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

    history_end_ms = int(time.time() * 1000)
    spot_meta = core.spot_meta()
    master_statement = _reconstruct_account(
        core, args.wallet, cutoff_ms, history_end_ms, spot_meta, current_spot, current_perp,
        staking_summary=staking_summary, vaults=vaults,
    )
    report["asset_statement"] = master_statement

    subaccount_statements: list[dict[str, Any]] = []
    for sub in subaccounts if isinstance(subaccounts, list) else []:
        if not isinstance(sub, dict) or not sub.get("subAccountUser"):
            continue
        address = str(sub["subAccountUser"])
        statement = _reconstruct_account(
            core, address, cutoff_ms, history_end_ms, spot_meta,
            sub.get("spotState") or core.spot_state(address),
            sub.get("clearinghouseState") or core.clearinghouse_state(address),
        )
        subaccount_statements.append({"name": str(sub.get("name") or "Subaccount"), "address": address, "statement": statement})

    report["subaccount_asset_statements"] = subaccount_statements
    report["combined_hypercore_asset_statement"] = _aggregate_statements(master_statement, subaccount_statements)

    report["hyperevm"] = {
        "status": "not_requested",
        "note": "Use --evm-explorer-key for event replay or --evm-rpc for archive-state queries.",
    }
    if args.evm_explorer_key:
        try:
            explorer_result = _explorer_hyperevm(args, cutoff_ms)
            if args.evm_rpc:
                token_contracts = [
                    str(row["contract"])
                    for row in explorer_result.get("erc20_balances") or []
                    if isinstance(row, dict) and row.get("contract")
                ]
                token_contracts.extend(args.erc20)
                try:
                    archive_result = _archive_hyperevm(
                        args.evm_rpc, args.wallet, cutoff_ms, token_contracts
                    )
                except Exception as exc:
                    archive_result = {
                        "status": "error",
                        "method": "archive_rpc",
                        "error": str(exc),
                    }
                explorer_result["archive_cross_check"] = archive_result
                comparison = _compare_hyperevm_results(explorer_result, archive_result)
                explorer_result["archive_comparison"] = comparison
                if comparison["status"] == "matched":
                    explorer_result["confidence"] = {
                        "level": "high_for_discovered_assets_archive_matched",
                        "note": (
                            "Native HYPE and every explorer-discovered or explicitly requested "
                            "token match archive state at the cutoff block. Token discovery "
                            "completeness still depends on the explorer index."
                        ),
                    }
                elif comparison["status"] == "mismatch":
                    explorer_result["confidence"] = {
                        "level": "review_required",
                        "note": "Explorer replay does not match archive state at the cutoff block.",
                    }
            report["hyperevm"] = explorer_result
        except Exception as exc:
            report["hyperevm"] = {"status": "error", "method": "explorer_event_replay", "error": str(exc)}
    elif args.evm_rpc:
        try:
            report["hyperevm"] = _archive_hyperevm(
                args.evm_rpc, args.wallet, cutoff_ms, args.erc20
            )
        except Exception as exc:
            report["hyperevm"] = {"status": "error", "method": "archive_rpc", "error": str(exc)}
    return report


def main() -> None:
    args = _parser().parse_args()
    report = run(args)
    output = Path(args.output)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
