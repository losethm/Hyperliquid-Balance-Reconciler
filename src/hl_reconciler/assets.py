from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any


ZERO = Decimal("0")


def dec(value: Any) -> Decimal:
    if value in (None, ""):
        return ZERO
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return ZERO


def _add(deltas: dict[str, Decimal], token: str | None, amount: Decimal) -> None:
    if not token or amount == ZERO:
        return
    deltas[str(token)] += amount


def current_spot_balances(spot_state: Any) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(Decimal)
    if not isinstance(spot_state, dict):
        return dict(out)
    for row in spot_state.get("balances") or []:
        if not isinstance(row, dict) or not row.get("coin"):
            continue
        _add(out, str(row["coin"]), dec(row.get("total")))
    return dict(out)


def current_staking_hype(staking_summary: Any) -> Decimal:
    if not isinstance(staking_summary, dict):
        return ZERO
    return sum(
        (
            dec(staking_summary.get("delegated")),
            dec(staking_summary.get("undelegated")),
            dec(staking_summary.get("totalPendingWithdrawal")),
        ),
        ZERO,
    )


def spot_pair_map(spot_meta: Any) -> dict[str, tuple[str, str]]:
    if not isinstance(spot_meta, dict):
        return {}
    tokens = {
        int(row["index"]): str(row["name"])
        for row in spot_meta.get("tokens") or []
        if isinstance(row, dict) and row.get("index") is not None and row.get("name")
    }
    out: dict[str, tuple[str, str]] = {}
    for row in spot_meta.get("universe") or []:
        if not isinstance(row, dict):
            continue
        pair_tokens = row.get("tokens") or []
        if len(pair_tokens) != 2:
            continue
        try:
            base = tokens[int(pair_tokens[0])]
            quote = tokens[int(pair_tokens[1])]
            index = int(row["index"])
        except (KeyError, TypeError, ValueError):
            continue
        pair = (base, quote)
        out[f"@{index}"] = pair
        if row.get("name"):
            out[str(row["name"])] = pair
        out[f"{base}/{quote}"] = pair
    return out


def _resolve_spot_pair(coin: str, pairs: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    pair = pairs.get(coin)
    if pair is not None:
        return pair

    # Hyperliquid outcome markets use #<encoding> for the traded spot coin and
    # +<encoding> for the corresponding token balance. Outcome books are quoted
    # in USDC, so they can be reconstructed with the same spot arithmetic.
    if coin.startswith("#") and coin[1:].isdigit():
        return f"+{coin[1:]}", "USDC"
    return None


def fill_forward_deltas(
    fills: Any,
    pairs: dict[str, tuple[str, str]],
) -> tuple[dict[str, Decimal], list[dict[str, Any]]]:
    """Compute token changes caused by fills in chronological-forward direction.

    Spot fills alter base and quote quantities. Perp fills only alter settlement cash
    through realized PnL and fees; open position notional/unrealized PnL is deliberately
    excluded from the asset-balance statement.
    """
    deltas: dict[str, Decimal] = defaultdict(Decimal)
    warnings: list[dict[str, Any]] = []
    for fill in fills if isinstance(fills, list) else []:
        if not isinstance(fill, dict):
            continue
        direction = str(fill.get("dir") or "")
        coin = str(fill.get("coin") or "")
        is_spot = direction in {"Buy", "Sell"}
        fee_token = str(fill.get("feeToken") or "USDC")
        # Hyperliquid documents `fee` as the total fee, inclusive of optional
        # builderFee. Do not add builderFee a second time.
        fee = dec(fill.get("fee"))

        if is_spot:
            pair = _resolve_spot_pair(coin, pairs)
            if pair is None:
                warnings.append({"type": "unknown_spot_pair", "coin": coin, "time": fill.get("time")})
                continue
            base, quote = pair
            size = dec(fill.get("sz"))
            quote_amount = size * dec(fill.get("px"))
            if str(fill.get("side")) == "B":
                _add(deltas, base, size)
                _add(deltas, quote, -quote_amount)
            else:
                _add(deltas, base, -size)
                _add(deltas, quote, quote_amount)
            _add(deltas, fee_token, -fee)
            continue

        settlement = fee_token or "USDC"
        _add(deltas, settlement, dec(fill.get("closedPnl")) - fee)
    return dict(deltas), warnings


def funding_forward_deltas(funding: Any) -> dict[str, Decimal]:
    deltas: dict[str, Decimal] = defaultdict(Decimal)
    for row in funding if isinstance(funding, list) else []:
        delta = row.get("delta") if isinstance(row, dict) else None
        if isinstance(delta, dict):
            _add(deltas, "USDC", dec(delta.get("usdc")))
    return dict(deltas)


def ledger_forward_deltas(
    ledger: Any,
    account: str,
) -> tuple[dict[str, Decimal], dict[str, int], list[dict[str, Any]]]:
    """Compute externally meaningful token deltas from non-funding ledger updates.

    Transfers between spot/perp/staking buckets are neutral when the report is measuring
    total assets owned by one address. Vault flows are applied to liquid USDC but flagged,
    because the corresponding historical vault equity requires a separate valuation.
    """
    addr = account.lower()
    deltas: dict[str, Decimal] = defaultdict(Decimal)
    counts: Counter[str] = Counter()
    warnings: list[dict[str, Any]] = []

    for row in ledger if isinstance(ledger, list) else []:
        delta = row.get("delta") if isinstance(row, dict) else None
        if not isinstance(delta, dict):
            continue
        typ = str(delta.get("type") or "unknown")
        counts[typ] += 1

        if typ == "deposit":
            _add(deltas, "USDC", abs(dec(delta.get("usdc"))))
        elif typ == "withdraw":
            _add(deltas, "USDC", -abs(dec(delta.get("usdc"))) - abs(dec(delta.get("fee"))))
        elif typ in {"internalTransfer", "subAccountTransfer"}:
            amount = abs(dec(delta.get("usdc")))
            source = str(delta.get("user") or "").lower()
            destination = str(delta.get("destination") or "").lower()
            if source == addr:
                _add(deltas, "USDC", -amount - abs(dec(delta.get("fee"))))
            if destination == addr:
                _add(deltas, "USDC", amount)
        elif typ in {"spotTransfer", "send"}:
            amount = abs(dec(delta.get("amount")))
            source = str(delta.get("user") or "").lower()
            destination = str(delta.get("destination") or "").lower()
            token = str(delta.get("token") or "")
            if source == addr:
                _add(deltas, token, -amount)
                _add(deltas, str(delta.get("feeToken") or ""), -abs(dec(delta.get("fee"))))
                _add(deltas, "HYPE", -abs(dec(delta.get("nativeTokenFee"))))
            if destination == addr:
                _add(deltas, token, amount)
        elif typ == "rewardsClaim":
            _add(deltas, str(delta.get("token") or ""), dec(delta.get("amount")))
        elif typ == "spotGenesis":
            _add(deltas, str(delta.get("token") or ""), dec(delta.get("amount")))
        elif typ == "deployGasAuction":
            _add(deltas, str(delta.get("token") or ""), -abs(dec(delta.get("amount"))))
        elif typ == "vaultCreate":
            _add(deltas, "USDC", -abs(dec(delta.get("usdc"))) - abs(dec(delta.get("fee"))))
            warnings.append({"type": "vault_flow", "ledger_type": typ, "time": row.get("time")})
        elif typ == "vaultDeposit":
            _add(deltas, "USDC", -abs(dec(delta.get("usdc"))))
            warnings.append({"type": "vault_flow", "ledger_type": typ, "time": row.get("time")})
        elif typ == "vaultWithdraw":
            _add(deltas, "USDC", dec(delta.get("netWithdrawnUsd")))
            warnings.append({"type": "vault_flow", "ledger_type": typ, "time": row.get("time")})
        elif typ in {"vaultDistribution", "vaultLeaderCommission"}:
            _add(deltas, "USDC", dec(delta.get("usdc")))
            warnings.append({"type": "vault_flow", "ledger_type": typ, "time": row.get("time")})
        elif typ in {"accountClassTransfer", "cStakingTransfer", "activateDexAbstraction"}:
            # Internal movement between buckets owned by the same address.
            pass
        elif typ == "borrowLend":
            # Principal moves between free and supplied/borrowed buckets. Interest is
            # reconstructed from userBorrowLendInterest instead of this action event.
            if str(delta.get("operation")) in {"borrow", "repay"}:
                warnings.append({"type": "borrow_lend_liability_flow", "time": row.get("time"), "delta": delta})
        elif typ == "liquidation":
            # Realized liquidation fills/fees are expected in the fill stream. Keeping
            # this neutral avoids double counting while preserving a diagnostic warning.
            warnings.append({"type": "liquidation_seen", "time": row.get("time")})
        else:
            warnings.append({"type": "unsupported_ledger_type", "ledger_type": typ, "time": row.get("time"), "delta": delta})

    return dict(deltas), dict(counts), warnings


def staking_reward_forward_deltas(rewards: Any, cutoff_ms: int) -> dict[str, Decimal]:
    total = ZERO
    for row in rewards if isinstance(rewards, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            time_ms = int(row.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if time_ms > cutoff_ms and str(row.get("source") or "") == "delegation":
            total += dec(row.get("totalAmount"))
    return {"HYPE": total} if total else {}


def borrow_lend_interest_forward_deltas(interest: Any, cutoff_ms: int) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    asset_deltas: dict[str, Decimal] = defaultdict(Decimal)
    liability_deltas: dict[str, Decimal] = defaultdict(Decimal)
    for row in interest if isinstance(interest, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            time_ms = int(row.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if time_ms <= cutoff_ms:
            continue
        token = str(row.get("token") or "")
        _add(asset_deltas, token, dec(row.get("supply")))
        _add(liability_deltas, token, dec(row.get("borrow")))
    return dict(asset_deltas), dict(liability_deltas)


def combine_deltas(*parts: dict[str, Decimal]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(Decimal)
    for part in parts:
        for token, amount in part.items():
            _add(out, token, amount)
    return dict(out)


def reconstruct_from_current(
    current_owned: dict[str, Decimal],
    forward_deltas: dict[str, Decimal],
) -> dict[str, Decimal]:
    tokens = set(current_owned) | set(forward_deltas)
    return {token: current_owned.get(token, ZERO) - forward_deltas.get(token, ZERO) for token in sorted(tokens)}


def serialize_balances(balances: dict[str, Decimal], dust: Decimal = Decimal("0.00000001")) -> dict[str, str]:
    return {
        token: format(amount, "f")
        for token, amount in sorted(balances.items())
        if abs(amount) >= dust
    }
