from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class HttpError(RuntimeError):
    pass


def post_json(url: str, payload: dict[str, Any], timeout: float = 30.0) -> Any:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "hl-balance-reconciler/0.1"},
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise HttpError(f"POST {url} failed: {exc}") from exc
