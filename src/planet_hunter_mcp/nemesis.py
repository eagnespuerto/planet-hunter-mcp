"""NEMESIS client (Vetstar v2 worker on a Hugging Face Space).

NEMESIS: Feliz et al. 2021 (AJ 161, 247) and NEMESIS II (Feliz et al. 2026), with LEO-Vetter.
The worker API (vetstar/nemesis_worker/worker/app.py):

    GET  /health                 -> {"ok", "nemesis_version", "queue_depth"}
    GET  /sectors/{tic}          -> sectors NEMESIS can run for this star
    POST /jobs                   -> {"job_id", "position"}
    GET  /jobs/{id}              -> state, stage, log tail
    GET  /jobs/{id}/result       -> summary JSON incl. base64 figures
    GET  /jobs/{id}/files[/{n}]  -> NEMESIS report pages (PDF/PNG) and raw FITS

Direct mode (NEMESIS_WORKER_TOKEN set) talks to the Space with an X-Worker-Token header.
Proxy mode goes through Vetstar's /api/nemesis/* routes, which map one to one onto the
worker (except /health -> /status) and add the token server-side.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx

from .config import (NEMESIS_URL, NEMESIS_WORKER_TOKEN, USER_AGENT, VETSTAR_TIMEOUT, VETSTAR_URL,
                     target_dir)


def mode() -> dict:
    if NEMESIS_WORKER_TOKEN:
        return {"mode": "direct", "base_url": NEMESIS_URL}
    return {"mode": "vetstar-proxy", "base_url": f"{VETSTAR_URL}/api/nemesis"}


async def _request(method: str, path: str, **kw) -> httpx.Response:
    m = mode()
    headers = {"User-Agent": USER_AGENT}
    if m["mode"] == "direct":
        headers["X-Worker-Token"] = NEMESIS_WORKER_TOKEN
    elif path == "/health":
        path = "/status"
    async with httpx.AsyncClient(base_url=m["base_url"], timeout=VETSTAR_TIMEOUT, headers=headers,
                                 follow_redirects=True) as c:
        r = await c.request(method, path, **kw)
    if r.status_code >= 400:
        detail = r.text[:400]
        try:
            detail = r.json().get("detail", detail)
        except Exception:  # noqa: BLE001
            pass
        raise httpx.HTTPStatusError(f"NEMESIS {path}: {r.status_code}: {detail}", request=r.request, response=r)
    return r


async def health() -> dict:
    return {**mode(), **(await _request("GET", "/health")).json()}


async def sectors(tic: int) -> dict:
    return (await _request("GET", f"/sectors/{tic}")).json()


async def submit(tic: int, sector: int, **opts: Any) -> dict:
    body = {"tic": tic, "sector": sector, **{k: v for k, v in opts.items() if v is not None}}
    if "cadence" not in body:
        body["cadence"] = "30 minute" if sector <= 26 else "10 minute"
    return (await _request("POST", "/jobs", json=body)).json()


async def status(job_id: str) -> dict:
    return (await _request("GET", f"/jobs/{_check_id(job_id)}")).json()


async def result(job_id: str) -> dict:
    return (await _request("GET", f"/jobs/{_check_id(job_id)}/result")).json()


async def files(job_id: str) -> list[dict]:
    return (await _request("GET", f"/jobs/{_check_id(job_id)}/files")).json()


async def download_file(job_id: str, name: str, out: Path) -> Path:
    r = await _request("GET", f"/jobs/{_check_id(job_id)}/files/{name}")
    out.write_bytes(r.content)
    return out


async def vetstar_report_pdf(job_id: str, out: Path, author: str | None = None,
                             initials: str | None = None) -> Path:
    """Vetstar's NEMESIS PDF (verdict, LEO-Vetter table, figures). Only Vetstar builds it, so this
    always goes through the Vetstar site, which must point at the same worker."""
    body = {"include_ffi": True, "author": author, "initials": initials}
    async with httpx.AsyncClient(base_url=VETSTAR_URL, timeout=VETSTAR_TIMEOUT,
                                 headers={"User-Agent": USER_AGENT}) as c:
        r = await c.post(f"/api/nemesis/jobs/{_check_id(job_id)}/report", json=body)
        r.raise_for_status()
    out.write_bytes(r.content)
    return out


def _check_id(job_id: str) -> str:
    if not job_id.isalnum():
        raise ValueError(f"Bad NEMESIS job id {job_id!r}: expected the hex id returned by ph_nemesis_run.")
    return job_id


def job_dir(tic: int, job_id: str) -> Path:
    d = target_dir(tic) / f"nemesis_{_check_id(job_id)[:12]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_figures(res: dict, out_dir: Path) -> dict[str, str]:
    """Write the result's base64 figures to PNG files; return {key: path}."""
    saved = {}
    for key, b64 in (res.get("figures") or {}).items():
        if isinstance(b64, str) and b64:
            p = out_dir / f"nemesis_{key}.png"
            p.write_bytes(base64.b64decode(b64.split(",", 1)[-1]))
            saved[key] = str(p)
    return saved


def strip_arrays(x: Any, max_len: int = 50) -> Any:
    """Drop figures and long arrays (light curve, periodogram, fold) so a summary fits in context."""
    if isinstance(x, dict):
        return {k: strip_arrays(v, max_len) for k, v in x.items() if k != "figures"}
    if isinstance(x, list):
        if len(x) > max_len:
            return f"<{len(x)} values omitted>"
        return [strip_arrays(v, max_len) for v in x]
    return x


def summary(res: dict) -> dict:
    """The numbers a planet hunter reads first: period, depth, radius, SDE, LEO-Vetter disposition."""
    primary = res.get("primary")
    s = (res.get("search") or {}).get(primary) or {}
    p = s.get("params") or {}
    leo = s.get("leo") or {}
    failed = [t.get("key") for t in leo.get("tests", []) if t.get("passed") is False]
    return dict(primary_method=primary, params=strip_arrays(p),
                leo_disposition=leo.get("disposition"), leo_failed_tests=failed,
                star=res.get("star"), meta=res.get("meta"))
