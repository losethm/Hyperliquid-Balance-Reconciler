from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import hashlib
import json
from typing import Any
from urllib.parse import urlencode

from .http import get_json


class ExplorerError(RuntimeError):
    pass


class EtherscanCompatibleExplorer:
    """Small client for Etherscan/Blockscout-compatible multichain APIs."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        chain_id: int = 999,
        timeout: float = 30.0,
        chain_param: str = "chain_id",
    ):
        self.base_url = base_url.rstrip("?")
        self.api_key = api_key
        self.chain_id = chain_id
        self.timeout = timeout
        self.chain_param = chain_param

    def _call(self, module: str, action: str, **params: Any) -> Any:
        query: dict[str, Any] = {
            self.chain_param: self.chain_id,
            "module": module,
            "action": action,
            "apikey": self.api_key,
            **params,
        }
        payload = get_json(f"{self.base_url}?{urlencode(query)}", timeout=self.timeout)
        if not isinstance(payload, dict):
            raise ExplorerError("Explorer returned non-object response")
        status = str(payload.get("status", "1"))
        result = payload.get("result")
        if status == "0" and not (isinstance(result, list) and not result):
            message = payload.get("message") or result or "unknown explorer error"
            # Etherscan-style APIs often use status=0 for a legitimate empty result.
            if isinstance(message, str) and "no transactions found" in message.lower():
                return []
            raise ExplorerError(str(message))
        return result

    def _account_history(
        self,
        action: str,
        address: str,
        start_block: int,
        end_block: int,
        page_size: int,
        max_pages: int,
    ) -> list[dict[str, Any]]:
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        if max_pages <= 0:
            raise ValueError("max_pages must be positive")

        rows: list[dict[str, Any]] = []
        page_signatures: set[bytes] = set()
        for page in range(1, max_pages + 1):
            result = self._call(
                "account",
                action,
                address=address,
                startblock=start_block,
                endblock=end_block,
                page=page,
                offset=page_size,
                sort="asc",
            )
            if not isinstance(result, list):
                raise ExplorerError(f"Explorer {action} returned a non-list result")
            if not all(isinstance(row, dict) for row in result):
                raise ExplorerError(f"Explorer {action} returned a non-object row")

            signature = hashlib.sha256(
                json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).digest()
            if result and signature in page_signatures:
                raise ExplorerError(
                    f"Explorer repeated page {page} for {action}; history completeness is unknown"
                )
            page_signatures.add(signature)
            rows.extend(result)
            if len(result) < page_size:
                return rows

        raise ExplorerError(
            f"Explorer {action} reached the {max_pages}-page safety limit; "
            "history completeness is unknown"
        )

    def block_by_timestamp(self, timestamp_s: int, closest: str = "before") -> int:
        result = self._call("block", "getblocknobytime", timestamp=timestamp_s, closest=closest)
        return int(result)

    def token_transfers(
        self,
        address: str,
        start_block: int = 0,
        end_block: int = 999999999,
        page_size: int = 1000,
        max_pages: int = 1_000,
    ) -> list[dict[str, Any]]:
        return self._account_history(
            "tokentx", address, start_block, end_block, page_size, max_pages
        )

    def normal_transactions(
        self,
        address: str,
        start_block: int = 0,
        end_block: int = 999999999,
        page_size: int = 1000,
        max_pages: int = 1_000,
    ) -> list[dict[str, Any]]:
        return self._account_history(
            "txlist", address, start_block, end_block, page_size, max_pages
        )

    def internal_transactions(
        self,
        address: str,
        start_block: int = 0,
        end_block: int = 999999999,
        page_size: int = 1000,
        max_pages: int = 1_000,
    ) -> list[dict[str, Any]]:
        return self._account_history(
            "txlistinternal", address, start_block, end_block, page_size, max_pages
        )


def replay_erc20_balances(address: str, transfers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Replay ERC-20 Transfer events through a cutoff block.

    The transfer endpoint already returns human metadata plus raw integer values, so
    no historical eth_call is required for plain ERC-20 balances.
    """
    target = address.lower()
    raw: dict[str, int] = defaultdict(int)
    meta: dict[str, dict[str, Any]] = {}

    for row in transfers:
        contract = str(row.get("contractAddress") or "").lower()
        if not contract:
            continue
        value = int(str(row.get("value") or "0"))
        from_addr = str(row.get("from") or "").lower()
        to_addr = str(row.get("to") or "").lower()
        if to_addr == target:
            raw[contract] += value
        if from_addr == target:
            raw[contract] -= value
        meta[contract] = {
            "contract": contract,
            "symbol": row.get("tokenSymbol"),
            "name": row.get("tokenName"),
            "decimals": int(str(row.get("tokenDecimal") or "0")),
        }

    out: dict[str, dict[str, Any]] = {}
    for contract, value in raw.items():
        decimals = meta[contract]["decimals"]
        if decimals < 0:
            raise ExplorerError(f"Token {contract} reported negative decimals")
        qty = Decimal(value) / (Decimal(10) ** decimals)
        out[contract] = {
            **meta[contract],
            "raw_balance": str(value),
            "balance": format(qty, "f"),
        }
    return out


def replay_native_hype(
    address: str,
    normal_txs: list[dict[str, Any]],
    internal_txs: list[dict[str, Any]],
) -> Decimal:
    """Reconstruct native HYPE from genesis through the cutoff.

    Counts external/internal value transfers and gas paid by successful external
    transactions sent by the target address. HyperEVM system transactions may require
    an archive-provider cross-check if an explorer omits them.
    """
    target = address.lower()
    wei = 0
    for row in normal_txs:
        from_addr = str(row.get("from") or "").lower()
        to_addr = str(row.get("to") or "").lower()
        value = int(str(row.get("value") or "0"))
        succeeded = str(row.get("isError") or "0") == "0"

        if from_addr == target:
            gas_used = int(str(row.get("gasUsed") or "0"))
            gas_price = int(str(row.get("gasPrice") or "0"))
            wei -= gas_used * gas_price
            if succeeded:
                wei -= value
        if succeeded and to_addr == target:
            wei += value

    for row in internal_txs:
        if str(row.get("isError") or "0") != "0":
            continue
        from_addr = str(row.get("from") or "").lower()
        to_addr = str(row.get("to") or "").lower()
        value = int(str(row.get("value") or "0"))
        if to_addr == target:
            wei += value
        if from_addr == target:
            wei -= value

    return Decimal(wei) / Decimal(10**18)
