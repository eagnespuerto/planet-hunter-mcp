"""Vetstar (TESS Vetting Studio) client, report-PDF parser and triage red flags (spec S1).

Vetstar: https://github.com/eagnespuerto/vetstar, live at https://vetstar.onrender.com.
The API and the PDF report are normalised to one schema so the same red-flag checks run on both.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx

from .config import USER_AGENT, VETSTAR_TIMEOUT, VETSTAR_URL, target_dir

# ------------------------------------------------------------------------- API client

_DROP_KEYS = {"plots", "lightcurve", "_periodogram"}


def _strip(x: Any) -> Any:
    """Remove base64 plots, light-curve arrays and periodograms from an API response."""
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if k not in _DROP_KEYS}
    if isinstance(x, list):
        return [_strip(v) for v in x]
    return x


async def _request(method: str, path: str, **kw) -> httpx.Response:
    async with httpx.AsyncClient(base_url=VETSTAR_URL, timeout=VETSTAR_TIMEOUT,
                                 headers={"User-Agent": USER_AGENT}) as c:
        r = await c.request(method, path, **kw)
        if r.status_code >= 400:
            detail = r.text[:400]
            try:
                detail = r.json().get("detail", detail)
            except Exception:  # noqa: BLE001
                pass
            raise httpx.HTTPStatusError(f"Vetstar {path}: {r.status_code}: {detail}", request=r.request, response=r)
        return r


async def health() -> dict:
    return (await _request("GET", "/api/health")).json()


async def sectors(tic: int) -> dict:
    return (await _request("GET", f"/api/mast/sectors/{tic}")).json()


async def analyze(tic: int, sector: int | None, options: dict) -> dict:
    """Single sector → /api/mast/analyze; sector=None → /api/mast/multisector."""
    if sector is None:
        body = {"tic_id": tic, **options}
        res = (await _request("POST", "/api/mast/multisector", json=body)).json()
    else:
        body = {"tic_id": tic, "sector": sector, **options}
        res = (await _request("POST", "/api/mast/analyze", json=body)).json()
    return _strip(res)


async def download_report(tic: int, sector: int | None, options: dict, out_dir: Path | None = None) -> Path:
    """Build the Vetstar PDF report server-side and save it locally."""
    if sector is None:
        r = await _request("POST", "/api/mast/multisector/report", json={"tic_id": tic, **options})
        name = f"vetting_TIC{tic}_multisector.pdf"
    else:
        r = await _request("POST", "/api/mast/report", json={"tic_id": tic, "sector": sector, **options})
        name = f"vetting_TIC{tic}_S{sector:03d}.pdf"
    out = (out_dir or target_dir(tic)) / name
    out.write_bytes(r.content)
    return out


# ------------------------------------------------------------------------- PDF parser

_NUM = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"

# label in the PDF → normalised key. The value is on the line after the label.
_LABELS = {
    "TIC ID": "tic", "Tmag": "tmag", "Teff (K)": "teff", "Radius (R_sun)": "rstar", "log g (cgs)": "logg",
    "CROWDSAP": "crowdsap", "Sector / Camera / CCD": "sector_camera_ccd", "RA, Dec (deg)": "radec",
    "Discrete dip events": "n_dips", "Photometric scatter (MAD)": "scatter_mad",
    "Time span (d)": "time_span_d",
    "BLS period (d)": "bls_period", "BLS t0 (BTJD)": "bls_t0", "BLS duration (d)": "bls_duration_d",
    "BLS depth": "bls_depth", "BLS SDE": "bls_sde", "BLS transits in window": "bls_transits_in_window",
    "Lomb-Scargle top period (d)": "ls_period", "Lomb-Scargle top power": "ls_power",
    "Column shift (σ)": "centroid_col_sigma", "Row shift (σ)": "centroid_row_sigma", "On target?": "on_target",
    "Depth — odd transits": "odd_depth", "Depth — even transits": "even_depth", "Difference σ": "odd_even_sigma",
    "EB flag?": "odd_even_flag", "Depth at phase 0.5": "secondary_depth", "Significance σ": "secondary_sigma",
    "Detected?": "secondary_detected", "T14 (h)": "t14_h", "T23 (h, flat bottom)": "t23_h",
    "Shape class": "shape_class", "Observed depth": "observed_depth",
    "Dilution-corrected depth": "corrected_depth", "Companion radius (R_sun)": "r2_rsun",
    "Companion radius (R_Jup)": "r2_rjup", "Category": "physics_category",
    "Planet candidate?": "planet_candidate", "Transit Depth ***": "exofop_depth_ppm",
    "Orbital Period ***": "exofop_period", "Transit Epoch ***": "exofop_epoch_bjd",
    # multi-sector reports
    "Sectors observed": "n_sectors_observed", "Sectors with detections": "n_sectors_with_detections",
    "Consensus period (d)": "consensus_period", "Objects identified": "n_objects",
}

# Labels that also appear as table headers elsewhere: accept the value only if it matches.
_VALUE_GUARDS = {"physics_category": re.compile(r"sized|Stellar", re.I)}


def _val(s: str) -> Any:
    s = s.strip()
    if re.fullmatch(_NUM, s):
        return float(s)
    if s.lower() in ("yes", "no"):
        return s.lower() == "yes"
    return s


def parse_report_text(text: str) -> dict:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out: dict[str, Any] = {}
    for i, ln in enumerate(lines[:-1]):
        key = _LABELS.get(ln)
        if key and key not in out:
            guard = _VALUE_GUARDS.get(key)
            if guard and not guard.search(lines[i + 1]):
                continue
            out[key] = _val(lines[i + 1])
    m = re.search(r"Category:\s*(\S+)\s+Confidence:\s*(\d+)%", text)
    if m:
        out["verdict_category"], out["verdict_confidence"] = m.group(1), int(m.group(2)) / 100
    if isinstance(out.get("sector_camera_ccd"), str):
        parts = re.findall(r"\d+", out["sector_camera_ccd"])
        if parts:
            out["sector"] = int(parts[0])
    if isinstance(out.get("radec"), str):
        nums = re.findall(_NUM, out["radec"])
        if len(nums) == 2:
            out["ra"], out["dec"] = float(nums[0]), float(nums[1])
    if "tic" not in out:
        m = re.search(r"TIC\s+(\d+)", text)
        if m:
            out["tic"] = m.group(1)
    if "tic" in out:
        out["tic"] = int(out["tic"])
    m = re.search(r"Verdict:\s*(.+?)(?:\s{2,}Category:|$)", text, re.M)
    if m:
        out["verdict"] = m.group(1).strip()
    # Dip table: rows of "#, t_start, t_end, Dur (h), Depth (%)" after the "Depth (%)" header.
    dips = []
    if "Depth (%)" in lines:
        j = lines.index("Depth (%)") + 1
        while j + 4 < len(lines) and all(re.fullmatch(_NUM, lines[j + k]) for k in range(5)):
            n, ts, te, dur, dep = (float(lines[j + k]) for k in range(5))
            dips.append(dict(n=int(n), t_start=ts, t_end=te, t_mid=(ts + te) / 2, dur_h=dur, depth=dep / 100))
            j += 5
    out["dips"] = dips
    m = re.search(r"SPOC DV[^\n]*?depth[^\n]*?(" + _NUM + r")\s*ppm", text, re.I)
    if m:
        out["dvt_depth_ppm"] = float(m.group(1))
    return out


def parse_report(pdf_path: str | Path) -> dict:
    import pymupdf
    doc = pymupdf.open(str(pdf_path))
    text = "\n".join(p.get_text() for p in doc)
    parsed = parse_report_text(text)
    parsed["n_pages"] = doc.page_count
    parsed["source"] = str(pdf_path)
    return parsed


def render_pages(pdf_path: str | Path, out_dir: Path, dpi: int = 90) -> list[str]:
    import pymupdf
    doc = pymupdf.open(str(pdf_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, p in enumerate(doc):
        fp = out_dir / f"{Path(pdf_path).stem}_p{i + 1:02d}.png"
        p.get_pixmap(dpi=dpi).save(str(fp))
        paths.append(str(fp))
    return paths


def normalise_api_result(res: dict) -> dict:
    """Map a Vetstar /api/mast/analyze response onto the PDF-parser schema."""
    star, bls, ls = res.get("star", {}), res.get("bls", {}), res.get("lomb_scargle", {})
    oe, sec, cen = res.get("odd_even", {}), res.get("secondary", {}), res.get("centroid", {})
    phys, shape, ver = res.get("physics", {}), res.get("shape", {}), res.get("verdict", {})
    ls_period = ls.get("top_period") or ls.get("period")
    if not ls_period and ls.get("tops"):
        ls_period = ls["tops"][0].get("period")
    dips = [dict(n=i + 1, t_start=e["t_start"], t_end=e["t_end"], t_mid=(e["t_start"] + e["t_end"]) / 2,
                 dur_h=e.get("duration_d", 0) * 24, depth=e.get("depth")) for i, e in enumerate(res.get("events", []))]
    out = dict(tic=star.get("tic_id"), sector=star.get("sector"), tmag=star.get("tmag"), teff=star.get("teff"),
               rstar=star.get("radius"), crowdsap=star.get("crowdsap"), ra=star.get("ra"), dec=star.get("dec"),
               n_dips=len(dips), dips=dips,
               bls_period=bls.get("period"), bls_t0=bls.get("t0"), bls_depth=bls.get("depth"),
               bls_sde=bls.get("sde"), bls_transits_in_window=bls.get("n_transits_in_window"),
               ls_period=ls_period,
               centroid_col_sigma=cen.get("shift_col_sigma"), centroid_row_sigma=cen.get("shift_row_sigma"),
               on_target=cen.get("on_target"),
               odd_depth=oe.get("depth_odd"), even_depth=oe.get("depth_even"), odd_even_sigma=oe.get("sigma"),
               odd_even_flag=oe.get("flag_eb"),
               secondary_depth=sec.get("depth"), secondary_sigma=sec.get("sigma"),
               secondary_detected=sec.get("detected"),
               t14_h=shape.get("t14_h") or shape.get("T14_h"), shape_class=shape.get("shape_class"),
               observed_depth=phys.get("observed_depth"), corrected_depth=phys.get("dilution_corrected_depth"),
               r2_rsun=phys.get("R_companion_Rsun"), r2_rjup=phys.get("R_companion_Rjup"),
               physics_category=phys.get("category"), planet_candidate=phys.get("is_planet_candidate"),
               verdict=ver.get("headline"), verdict_category=ver.get("category"),
               verdict_confidence=ver.get("confidence"), verdict_reasons=ver.get("reasons"))
    dvt = res.get("dvt") or {}
    tce = dvt.get("tce") or {}
    for k, v in tce.items():
        if "depth" in k.lower() and isinstance(v, (int, float)):
            out["dvt_depth_ppm"] = float(v)
            break
    if res.get("mast"):
        out["mast"] = res["mast"]
    return out


# ------------------------------------------------------------------------- red flags

def red_flags(r: dict) -> dict:
    """Spec S1 'Decide' checks: is the vetting report itself trustworthy?"""
    flags: list[dict] = []

    def add(code: str, msg: str, severity: str = "warn"):
        flags.append(dict(code=code, severity=severity, message=msg))

    dips = r.get("dips") or []
    n_dips = int(r.get("n_dips") or len(dips))
    tw = r.get("bls_transits_in_window")
    if n_dips <= 1 and tw is not None and tw >= 2:
        add("bls_period_artifact", f"Only {n_dips} discrete dip but BLS claims {int(tw)} transits in window: "
            "the BLS period is an artefact; do not use it.", "high")

    od, ev = r.get("odd_depth"), r.get("even_depth")
    if isinstance(od, (int, float)) and isinstance(ev, (int, float)) and max(abs(od), abs(ev)) > 0:
        if min(abs(od), abs(ev)) < 0.1 * max(abs(od), abs(ev)):
            add("odd_even_zero_depth", f"Odd/even depths {od:.5g} vs {ev:.5g}: one is ≈0, which means the "
                "period is wrong (e.g. 2× true), not that this is an EB.", "high")

    d = [x["depth"] for x in dips if x.get("depth")]
    if len(d) >= 4:
        a, b = d[0::2], d[1::2]
        separated = min(a) > max(b) or min(b) > max(a)
        ratio = max(sum(a) / len(a), sum(b) / len(b)) / max(min(sum(a) / len(a), sum(b) / len(b)), 1e-9)
        if separated and ratio > 1.3 and not r.get("odd_even_flag"):
            add("odd_even_blind", "Dip depths alternate (deep/shallow) but the odd/even test passed: the test was "
                "probably computed over primaries only at 2× the true period. Treat as an EB indicator "
                "(primary + secondary).", "high")

    ed = r.get("exofop_depth_ppm")
    if ed is not None and abs(float(ed) - 1e6) < 1:
        add("placeholder_depth", "depth_ppm = 1e6: placeholder/broken feature values; re-derive depths.", "high")

    dv = r.get("dvt_depth_ppm")
    meas = r.get("observed_depth") or (max((x["depth"] for x in dips), default=None) if dips else None)
    if dv and meas:
        ratio = max(dv / 1e6, meas) / max(min(dv / 1e6, meas), 1e-9)
        if ratio > 1.5:
            add("dv_depth_mismatch", f"SPOC DV depth {dv:.0f} ppm vs measured {meas * 1e6:.0f} ppm (×{ratio:.1f}): "
                "planet-model misfit, typical of an EB.")

    P, lsP = r.get("bls_period"), r.get("ls_period")
    if P and lsP and lsP < P:
        n = P / lsP
        if 2 <= round(n) <= 5 and abs(n - round(n)) / n < 0.02:
            add("ls_harmonic", f"Lomb-Scargle top period {lsP:.4g} d ≈ P/{round(n)}: harmonic, ignore.", "info")

    rj = r.get("r2_rjup")
    if isinstance(rj, (int, float)) and rj > 2:
        add("too_large_for_planet", f"Companion radius {rj:.2f} R_Jup > 2 R_Jup: not a planet.", "high")
    for k in ("centroid_col_sigma", "centroid_row_sigma"):
        v = r.get(k)
        if isinstance(v, (int, float)) and abs(v) > 3:
            add("centroid_offset", f"{k} = {v:.2f}σ (>3σ): possible off-target source; run ph_contamination.", "high")
    if r.get("secondary_detected"):
        add("secondary_detected", "Secondary eclipse detected at phase 0.5: EB indicator; measure depth ratio for T2.")

    hi = [f for f in flags if f["severity"] == "high"]
    return dict(n_flags=len(flags), n_high=len(hi), flags=flags,
                report_reliable=not any(f["code"] in ("bls_period_artifact", "odd_even_zero_depth",
                                                        "odd_even_blind", "placeholder_depth") for f in flags),
                gate=dict(target_identified=bool(r.get("tic") and (r.get("ra") is not None)),
                          dip_list_extracted=bool(dips)))
