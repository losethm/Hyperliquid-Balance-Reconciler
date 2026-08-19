from __future__ import annotations

import argparse
import json
from pathlib import Path

from .hypercore import HyperCoreClient
from .hyperevm import EvmRpcClient, RpcError, find_block_at_or_before, wei_to_hype
from .report import build_core_report
from .timeutils import parse_local_cutoff, to_millis


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hl-balance",
        description="Historical Hyperliquid balance diagnostics",
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
        help="Collect Core events +/- this many hours around cutoff",
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


def run(args: argparse.Namespace) -> dict:
    cutoff = parse_local_cutoff(args.at, args.timezone)
    cutoff_ms = to_millis(cutoff)
    radius_ms = max(args.window_hours, 0) * 60 * 60 * 1000
    start_ms, end_ms = cutoff_ms - radius_ms, cutoff_ms + radius_ms

    core = HyperCoreClient(args.info_url)
    report = build_core_report(
        wallet=args.wallet,
        cutoff_ms=cutoff_ms,
        event_start_ms=start_ms,
        event_end_ms=end_ms,
        portfolio=core.portfolio(args.wallet),
        funding=core.funding(args.wallet, start_ms, end_ms),
        ledger=core.ledger(args.wallet, start_ms, end_ms),
        fills=core.fills(args.wallet, start_ms, end_ms),
        current_perp=core.clearinghouse_state(args.wallet),
        current_spot=core.spot_state(args.wallet),
        subaccounts=core.subaccounts(args.wallet),
        vaults=core.vault_equities(args.wallet),
        staking_summary=core.staking_summary(args.wallet),
        staking_history=core.staking_history(args.wallet),
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
