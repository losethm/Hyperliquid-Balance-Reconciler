# Hyperliquid Balance Reconciler

Auditable tooling for reconstructing a Hyperliquid wallet's historical HyperCore and HyperEVM balances at a specified timestamp.

The project is intentionally conservative: if a public API cannot prove historical completeness, the report says so rather than presenting an estimate as exact.

## Example

```bash
hl-balance \
  0xYOUR_WALLET_ADDRESS \
  --at 2026-01-01T00:00:00 \
  --timezone UTC \
  --window-hours 24 \
  --output historical_balance.json
```

## Year-end asset statement

The primary report focuses on assets owned at the cutoff rather than valuing open perpetual positions. It works backwards from current token balances and reverses post-cutoff balance-changing activity.

Included in the reconstruction:

- HyperCore spot token balances and USDC/cash
- separate Standard-mode perp cash when applicable
- HYPE held in staking
- spot buys and sells
- realized perp PnL and fees
- funding payments
- deposits, withdrawals, and Core transfers
- staking rewards
- borrow/lend supply interest
- linked subaccounts, with per-account confidence diagnostics

Open perp notional and unrealized PnL are not part of the primary asset balance. They remain diagnostic context only.

## Important limitations

Hyperliquid's public history endpoints have response and global history limits. The reconciler splits busy time windows and flags evidence of missing history, but archival data remains the final fallback for an audit-grade reconstruction.

Hyperliquid's default HyperEVM RPC does not provide historical-state calls. Exact historical HyperEVM balances need an independent archive RPC or indexed replay of raw HyperEVM blocks.

Historical vault equity requires separate valuation if vault flows are material.

## Install

Python 3.11+ is required.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -e .
```

## HyperEVM archive RPC

```bash
hl-balance \
  0xYOUR_WALLET_ADDRESS \
  --at 2026-01-01T00:00:00 \
  --timezone UTC \
  --evm-rpc "$HYPEREVM_ARCHIVE_RPC" \
  --output historical_balance.json
```

For known ERC-20 contracts, repeat `--erc20`.

## HyperEVM explorer replay

When an archive RPC is unavailable, the reconciler can rebuild balances from an Etherscan/Blockscout-compatible explorer. Keep credentials outside the command line and repository by supplying them through the environment.

The explorer path finds the last block at or before the cutoff, paginates through the wallet's ERC-20, normal-transaction, and internal-transaction history, and replays the flows. It aborts when pagination repeats or reaches its safety limit rather than presenting a potentially truncated result as complete.

ERC-20 replay is exact only when the explorer index is complete. Native HYPE also needs an archive-state cross-check because chain-specific or system balance changes may not appear in explorer account-history endpoints. Indexed token discovery can be paired with archive-state `balanceOf` calls for a stronger cutoff snapshot.

Private validation runs should pass credentials through GitHub Actions secrets or local environment variables; wallet-specific values should not be committed to public workflows or examples.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Roadmap to an audit-grade total

1. Validate the asset statement against target-date live/archive evidence.
2. Resolve any account whose public history cannot be reconstructed to the cutoff.
3. Reconstruct historical vault equity if vault flows are material.
4. Add historical token prices for a cutoff USD/CAD valuation.
5. Add archive-backed HyperEVM token discovery and historical balances.
6. Use HyperCore archive replay whenever public history cannot reach the cutoff.
