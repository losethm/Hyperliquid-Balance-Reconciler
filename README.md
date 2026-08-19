# Hyperliquid Balance Reconciler

Auditable tooling for estimating or reconstructing a Hyperliquid wallet's historical HyperCore and HyperEVM balances at a specified timestamp.

The project is intentionally conservative: if a public API cannot prove historical completeness, the report says so rather than presenting an estimate as exact.

## Target example

Wallet:

```text
0xC2a16805C137FA13FE9c02a86d83Ec4cc2BcC897
```

Requested cutoff:

```text
2026-06-01 00:00:00 America/Regina
= 2026-06-01 06:00:00 UTC
= 1780293600000 ms
```

## What the first version does

- Normalizes a local timestamp to UTC.
- Queries HyperCore portfolio history and brackets the cutoff with sampled account-value points.
- Collects funding, non-funding ledger updates, and fills around the cutoff.
- Captures current perp, spot, subaccount, vault, and staking state for later reconciliation.
- Flags the HyperCore fill-history limitation instead of assuming the public API is complete.
- With an archive-capable HyperEVM RPC, binary-searches the exact EVM block at or immediately before the cutoff and queries native HYPE plus explicitly supplied ERC-20 balances.
- Produces a JSON audit artifact containing source data and confidence notes.

## Important limitations

Hyperliquid documents that `userFillsByTime` returns at most 2,000 fills per response and only the 10,000 most recent fills are available. For an active wallet, public-API-only reconstruction may therefore be incomplete.

Hyperliquid's default HyperEVM RPC supports block lookup but does not support historical-state requests such as historical `eth_getBalance` or `eth_call`. Exact historical HyperEVM balances need an independent archive RPC or a local/indexed replay of raw HyperEVM block data.

The sampled `portfolio` history is useful evidence, but it is not itself an exact block-state snapshot.

## Install

Python 3.11+ is required.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -e .
```

## Run the June 1 target

HyperCore diagnostics only:

```bash
hl-balance \
  0xC2a16805C137FA13FE9c02a86d83Ec4cc2BcC897 \
  --at 2026-06-01T00:00:00 \
  --timezone America/Regina \
  --window-hours 24 \
  --output historical_balance_2026-06-01.json
```

With an archive-capable HyperEVM RPC:

```bash
hl-balance \
  0xC2a16805C137FA13FE9c02a86d83Ec4cc2BcC897 \
  --at 2026-06-01T00:00:00 \
  --timezone America/Regina \
  --evm-rpc "$HYPEREVM_ARCHIVE_RPC" \
  --output historical_balance_2026-06-01.json
```

For known ERC-20 contracts, repeat `--erc20`:

```bash
hl-balance ... \
  --evm-rpc "$HYPEREVM_ARCHIVE_RPC" \
  --erc20 0xTokenAddress1 \
  --erc20 0xTokenAddress2
```

## Tests

```bash
python -m unittest discover -s tests -v
```

## Roadmap to an audit-grade snapshot

1. Run this diagnostic report for the target wallet/cutoff.
2. Inspect the nearest portfolio samples and event density around the cutoff.
3. If fill coverage is insufficient, ingest Hyperliquid historical node fills / explorer data and replay the account to the cutoff.
4. Add token-decimal metadata and historical prices for a USD valuation layer.
5. Add archive-backed HyperEVM token discovery so ERC-20 positions do not need to be supplied manually.
6. Reconcile forward and backward calculations and emit an explicit variance/confidence score.
