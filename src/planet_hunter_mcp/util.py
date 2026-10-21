"""Shared HTTP, error-formatting and number-ledger helpers."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from .config import HTTP_TIMEOUT, USER_AGENT, target_dir


async def http_get(url: str, *, params: dict | None = None, headers: dict | None = None,
                   timeout: float = HTTP_TIMEOUT) -> httpx.Response:
    h = {"User-Agent": USER_AGENT, **(headers or {})}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
        r = await c.get(url, params=params, headers=h)
        r.raise_for_status()
        return r


async def http_post(url: str, *, data: dict | None = None, json_body: Any = None,
                    headers: dict | None = None, timeout: float = HTTP_TIMEOUT) -> httpx.Response:
    h = {"User-Agent": USER_AGENT, **(headers or {})}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
        r = await c.post(url, data=data, json=json_body, headers=h)
        r.raise_for_status()
        return r


def error_text(e: Exception, service: str) -> str:
    """Actionable one-line error for the calling agent."""
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        hint = {
            202: "the service returned a bot-challenge page; do NOT try to bypass it, ask the user to run the query",
            401: "missing or invalid credentials (check the API token env var)",
            403: "access denied",
            404: "not found; check the identifier",
            429: "rate limited; wait and retry",
        }.get(code, "server error; retry later" if code >= 500 else "request rejected")
        return f"Error from {service}: HTTP {code}: {hint}."
    if isinstance(e, httpx.TimeoutException):
        return f"Error from {service}: request timed out (free-tier services may need a retry after warming up)."
    return f"Error from {service}: {type(e).__name__}: {e}"


def jsonable(x: Any) -> Any:
    """Recursively convert numpy / astropy scalars and NaN to JSON-safe values."""
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, np.ndarray):
        return jsonable(x.tolist())
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        v = float(x)
        return None if not math.isfinite(v) else v
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if hasattr(x, "mask") and getattr(x, "mask", False) is True:
        return None
    return x


def dumps(x: Any) -> str:
    return json.dumps(jsonable(x), indent=1, ensure_ascii=False)


def record_numbers(tic: str | int, section: str, values: dict) -> Path:
    """Merge `values` into <workdir>/TIC.../paper_numbers.json under `section`.

    Every number that ends up in a manuscript must be traceable to this ledger (spec S7 gate).
    """
    p = target_dir(tic) / "paper_numbers.json"
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    data[section] = jsonable(values)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return p


def load_numbers(tic: str | int) -> dict:
    p = target_dir(tic) / "paper_numbers.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
