from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


class HttpError(RuntimeError):
    pass


def _redact_url(url: str) -> str:
    """Remove credentials and sensitive query values from error messages."""
    parts = urlsplit(url)
    hostname = parts.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    port = f":{parts.port}" if parts.port is not None else ""
    userinfo = "***@" if parts.username is not None else ""
    netloc = f"{userinfo}{hostname}{port}"
    sensitive_markers = ("key", "token", "secret", "signature", "password")
    query = urlencode(
        [
            (name, "REDACTED" if any(marker in name.lower() for marker in sensitive_markers) else value)
            for name, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def _request_json(req: Request, timeout: float, max_retries: int) -> Any:
    method = req.get_method()
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
            raise HttpError(f"{method} {_redact_url(req.full_url)} failed: {exc}") from exc
        except (URLError, TimeoutError) as exc:
            if attempt < min(max_retries, 2):
                time.sleep(2 ** attempt)
                continue
            raise HttpError(f"{method} {_redact_url(req.full_url)} failed: {exc}") from exc
    raise HttpError(f"{method} {_redact_url(req.full_url)} failed after retries")


def get_json(url: str, timeout: float = 30.0, max_retries: int = 6) -> Any:
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "hl-balance-reconciler/0.1"})
    return _request_json(req, timeout, max_retries)


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
    return _request_json(req, timeout, max_retries)
