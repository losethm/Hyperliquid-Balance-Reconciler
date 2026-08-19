from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .http import post_json


DEFAULT_INFO_URL = "https://api.hyperliquid.xyz/info"


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
