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


def find_block_at_or_before(
    client: EvmRpcClient,
    cutoff_s: int,
    low: int = 1,
    high: int | None = None,
) -> LocatedBlock:
    """Binary-search the last EVM block whose timestamp is <= cutoff_s.

    HyperEVM's RPC rejects block height 0, so the default lower bound is 1.
    """
    if high is None:
        high = client.block_number()
    if low < 1 or high < low:
        raise ValueError("invalid block search bounds")

    first = client.block(low)
    if _block_timestamp(first) > cutoff_s:
        raise ValueError("cutoff predates the lower search bound")

    best_number = low
    best_ts = _block_timestamp(first)
    left, right = low, high
    while left <= right:
        mid = (left + right) // 2
        block = client.block(mid)
        ts = _block_timestamp(block)
        if ts <= cutoff_s:
            best_number, best_ts = mid, ts
            left = mid + 1
        else:
            right = mid - 1
    return LocatedBlock(best_number, best_ts)


def wei_to_hype(wei: int) -> str:
    return format(Decimal(wei) / Decimal(10**18), "f")
