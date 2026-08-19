from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .http import post_json


DEFAULT_INFO_URL = "https://api.hyperliquid.xyz/info"
DAY_MS = 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class PortfolioPoint:
    timestamp_ms: int
    account_value: str


class HyperCoreClient:
    def __init__(self, info_url: str = DEFAULT_INFO_URL, timeout: float = 30.0):
        self.info_url = info_url
        self.timeout = timeout

    def _info(self, payload: dict[str, Any]) -> Any:
        return post_json(self.info_url, payload, self.timeout)

    def portfolio(self, user: str) -> Any:
        return self._info({"type": "portfolio", "user": user})

    def clearinghouse_state(self, user: str, dex: str = "") -> Any:
        return self._info({"type": "clearinghouseState", "user": user, "dex": dex})

    def spot_state(self, user: str) -> Any:
        return self._info({"type": "spotClearinghouseState", "user": user})

    def spot_meta(self) -> Any:
        return self._info({"type": "spotMeta"})

    def user_abstraction(self, user: str) -> Any:
        return self._info({"type": "userAbstraction", "user": user})

    def funding(self, user: str, start_ms: int, end_ms: int) -> Any:
        return self._info({"type": "userFunding", "user": user, "startTime": start_ms, "endTime": end_ms})

    def ledger(self, user: str, start_ms: int, end_ms: int) -> Any:
        return self._info({"type": "userNonFundingLedgerUpdates", "user": user, "startTime": start_ms, "endTime": end_ms})

    def fills(self, user: str, start_ms: int, end_ms: int) -> Any:
        return self._info({
            "type": "userFillsByTime",
            "user": user,
            "startTime": start_ms,
            "endTime": end_ms,
            "aggregateByTime": False,
        })

    def subaccounts(self, user: str) -> Any:
        return self._info({"type": "subAccounts", "user": user})

    def vault_equities(self, user: str) -> Any:
        return self._info({"type": "userVaultEquities", "user": user})

    def staking_summary(self, user: str) -> Any:
        return self._info({"type": "delegatorSummary", "user": user})

    def staking_history(self, user: str) -> Any:
        return self._info({"type": "delegatorHistory", "user": user})

    def staking_rewards(self, user: str) -> Any:
        return self._info({"type": "delegatorRewards", "user": user})

    def borrow_lend_state(self, user: str) -> Any:
        return self._info({"type": "borrowLendUserState", "user": user})

    def borrow_lend_interest(self, user: str) -> Any:
        return self._info({"type": "userBorrowLendInterest", "user": user})

    def _chunked_history(
        self,
        fetcher: Callable[[int, int], Any],
        start_ms: int,
        end_ms: int,
        chunk_ms: int,
        split_cap: int | None = None,
    ) -> list[dict[str, Any]]:
        if end_ms < start_ms:
            return []

        rows: list[dict[str, Any]] = []

        def fetch_window(a: int, b: int) -> None:
            result = fetcher(a, b)
            part = result if isinstance(result, list) else []
            if split_cap and len(part) >= split_cap and b > a:
                midpoint = a + (b - a) // 2
                fetch_window(a, midpoint)
                fetch_window(midpoint + 1, b)
                return
            rows.extend(x for x in part if isinstance(x, dict))

        cursor = start_ms
        while cursor <= end_ms:
            window_end = min(end_ms, cursor + chunk_ms - 1)
            fetch_window(cursor, window_end)
            cursor = window_end + 1

        # Inclusive windows and API aggregation can occasionally repeat an event. Keep
        # a stable canonical representation so exact duplicate events are counted once.
        deduped: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = str(row.get("tid")) if row.get("tid") is not None else json.dumps(row, sort_keys=True, separators=(",", ":"))
            deduped[key] = row
        return sorted(deduped.values(), key=lambda row: (int(row.get("time") or 0), str(row.get("tid") or row.get("hash") or "")))

    def fills_range(self, user: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        # Time-range endpoints are paginated/truncated. Recursively split any window
        # that returns 500 fills so no page boundary is silently treated as complete.
        # This is intentionally more conservative than relying only on the 2,000 hard
        # userFillsByTime response ceiling.
        return self._chunked_history(
            lambda a, b: self.fills(user, a, b),
            start_ms,
            end_ms,
            7 * DAY_MS,
            split_cap=500,
        )

    def funding_range(self, user: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        return self._chunked_history(
            lambda a, b: self.funding(user, a, b),
            start_ms,
            end_ms,
            30 * DAY_MS,
            split_cap=500,
        )

    def ledger_range(self, user: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        return self._chunked_history(
            lambda a, b: self.ledger(user, a, b),
            start_ms,
            end_ms,
            30 * DAY_MS,
            split_cap=500,
        )


def portfolio_series(portfolio: Any, period: str = "allTime") -> list[PortfolioPoint]:
    if not isinstance(portfolio, list):
        return []
    periods = {name: body for name, body in portfolio if isinstance(name, str) and isinstance(body, dict)}
    body = periods.get(period) or {}
    history = body.get("accountValueHistory") or []
    out: list[PortfolioPoint] = []
    for row in history:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            try:
                out.append(PortfolioPoint(int(row[0]), str(row[1])))
            except (TypeError, ValueError):
                continue
    return sorted(out, key=lambda p: p.timestamp_ms)


def bracket_points(points: Iterable[PortfolioPoint], cutoff_ms: int) -> tuple[PortfolioPoint | None, PortfolioPoint | None]:
    before: PortfolioPoint | None = None
    after: PortfolioPoint | None = None
    for point in sorted(points, key=lambda p: p.timestamp_ms):
        if point.timestamp_ms <= cutoff_ms:
            before = point
        elif after is None:
            after = point
            break
    return before, after


def fill_coverage(fills: Any, requested_start_ms: int, requested_end_ms: int) -> dict[str, Any]:
    """Return coverage diagnostics without claiming completeness we cannot prove.

    Hyperliquid documents a 2,000-fill response cap and availability limited to the
    10,000 most recent fills. Hitting 2,000 means this window is definitely truncated.
    Receiving fewer than 2,000 does not prove that older fills remain available.
    """
    rows = fills if isinstance(fills, list) else []
    times = [int(x["time"]) for x in rows if isinstance(x, dict) and x.get("time") is not None]
    return {
        "requested_start_ms": requested_start_ms,
        "requested_end_ms": requested_end_ms,
        "count": len(rows),
        "earliest_returned_ms": min(times) if times else None,
        "latest_returned_ms": max(times) if times else None,
        "response_cap_hit": len(rows) >= 2000,
        "complete": False,
        "complete_reason": (
            "The public API cannot prove historical fill completeness because only the 10,000 most recent fills are available."
        ),
    }
