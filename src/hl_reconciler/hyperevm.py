from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .http import post_json


DEFAULT_RPC_URL = "https://rpc.hyperliquid.xyz/evm"


class RpcError(RuntimeError):
    pass


class EvmRpcClient:
    def __init__(self, rpc_url: str = DEFAULT_RPC_URL, timeout: float = 30.0):
        self.rpc_url = rpc_url
        self.timeout = timeout
        self._id = 0

    def call(self, method: str, params: list[Any]) -> Any:
        self._id += 1
        response = post_json(
            self.rpc_url,
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params},
            self.timeout,
        )
        if not isinstance(response, dict):
            raise RpcError(f"Invalid JSON-RPC response for {method}")
        if response.get("error") is not None:
            raise RpcError(f"{method} failed: {response['error']}")
        return response.get("result")

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def block(self, number: int) -> dict[str, Any]:
        result = self.call("eth_getBlockByNumber", [hex(number), False])
        if not isinstance(result, dict):
            raise RpcError(f"Block {number} not found")
        return result

    def native_balance(self, address: str, block_number: int | str = "latest") -> int:
        tag = hex(block_number) if isinstance(block_number, int) else block_number
        return int(self.call("eth_getBalance", [address, tag]), 16)

    def erc20_balance(self, token: str, address: str, block_number: int) -> int:
        selector = "70a08231"  # balanceOf(address)
        encoded_address = address.lower().removeprefix("0x").rjust(64, "0")
        result = self.call(
            "eth_call",
            [{"to": token, "data": "0x" + selector + encoded_address}, hex(block_number)],
        )
        return int(result, 16)


@dataclass(frozen=True)
class LocatedBlock:
    number: int
    timestamp_s: int


def _block_timestamp(block: dict[str, Any]) -> int:
    return int(block["timestamp"], 16)


def _is_invalid_height(exc: Exception) -> bool:
    return "invalid block height" in str(exc).lower() or "block not found" in str(exc).lower()


def find_block_at_or_before(
    client: EvmRpcClient,
    cutoff_s: int,
    low: int = 0,
    high: int | None = None,
) -> LocatedBlock:
    """Binary-search the last existing EVM block whose timestamp is <= cutoff_s.

    HyperEVM can use a nonzero EVM genesis height. Nonexistent lower heights are
    treated as pre-genesis and skipped during the search.
    """
    if high is None:
        high = client.block_number()
    if low < 0 or high < low:
        raise ValueError("invalid block search bounds")

    best_number: int | None = None
    best_ts: int | None = None
    left, right = low, high
    while left <= right:
        mid = (left + right) // 2
        try:
            block = client.block(mid)
        except RpcError as exc:
            if _is_invalid_height(exc):
                left = mid + 1
                continue
            raise
        ts = _block_timestamp(block)
        if ts <= cutoff_s:
            best_number, best_ts = mid, ts
            left = mid + 1
        else:
            right = mid - 1

    if best_number is None or best_ts is None:
        raise ValueError("cutoff predates the first available HyperEVM block")
    return LocatedBlock(best_number, best_ts)


def wei_to_hype(wei: int) -> str:
    return format(Decimal(wei) / Decimal(10**18), "f")
