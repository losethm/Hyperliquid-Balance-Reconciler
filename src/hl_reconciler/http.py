from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class HttpError(RuntimeError):
    pass


def post_json(
    url: str,
    payload: dict[str, Any],
    timeout: float = 30.0,
    max_retries: int = 6,
) -> Any:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "hl-balance-reconciler/0.1"},
    )
    for attempt in range(max_retries + 1):
        try:
            with urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 429 and attempt < max_retries:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = float(retry_after) if retry_after else min(2 ** attempt, 30)
                except ValueError:
                    delay = min(2 ** attempt, 30)
                time.sleep(max(delay, 1.0))
                continue
            raise HttpError(f"POST {url} failed: {exc}") from exc
        except (URLError, TimeoutError) as exc:
            if attempt < min(max_retries, 2):
                time.sleep(2 ** attempt)
                continue
            raise HttpError(f"POST {url} failed: {exc}") from exc
    raise HttpError(f"POST {url} failed after retries")
