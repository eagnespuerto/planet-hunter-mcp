"""Planet Hunter MCP server.

Tools follow the AGENT_SPEC pipeline: S1 triage (Vetstar) → S2 identity/contamination → S3 literature →
S4 MAST data → S5 eclipses → S6 ephemeris/aliases → S7 secondary/orbit → S8 figure → S9 manuscript →
S10 references → S11 RNAAS folder → S12 Zenodo bundle. Nothing is ever submitted or uploaded: the
tools prepare files and a human clicks Publish/Submit.
"""
from __future__ import annotations

import argparse
import json
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
import numpy as np
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from . import analysis, catalogs, lightcurves, manuscript, references, vetstar, zenodo
from .config import BTJD_OFFSET, MAST_DATASET_DOIS, VETSTAR_URL, WORKDIR, normalize_tic, target_dir
from .figures import rnaas_figure
from .util import dumps, error_text, load_numbers, record_numbers

INSTRUCTIONS = """Planet Hunter: tools for vetting TESS transit / eclipsing-binary candidates and turning the
result into a submission-ready RNAAS note and a Zenodo preprint bundle.

Typical order: ph_vetstar_vet or ph_vetstar_parse_report (S1) → ph_resolve_target + ph_contamination (S2) →
ph_vizier_sweep, ph_simbad_references, ph_exofop, ph_exoplanet_archive_cone, ph_ads_search (S3, BEFORE any
novelty claim) → ph_mast_download_lightcurves (S4) → ph_find_eclipses (S5) → ph_fit_ephemeris (S6) →
ph_primary_stack (S7) → ph_secondary_search (S7) → ph_make_figure (S8) → ph_rnaas_scaffold / ph_rnaas_check /
ph_latex_build (S9) → ph_verify_references, ph_uat_search (S10) → ph_rnaas_submission_folder (S11) →
ph_zenodo_prepare_bundle (S12) → ph_acceptance_checklist.

Authority rules: never submit, upload, publish or push; never bypass bot detection (ADS 202 pages, CAPTCHAs);
never state a DOI, catalogue value or 'first detection' claim not verified in this run; every number in a
manuscript must come from paper_numbers.json (ph_paper_numbers). Read resource planet-hunter://spec for the
full pipeline contract."""

mcp = MCPServer("planet_hunter_mcp", instructions=INSTRUCTIONS, version="0.1.0",
                website_url="https://github.com/eagnespuerto/planet-hunter-mcp")

READ_REMOTE = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
READ_LOCAL = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE_LOCAL = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE_REMOTE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
REPLACE_LOCAL = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)

TIC = Annotated[str, Field(description="TIC ID, e.g. '402175877' or 'TIC 402175877'")]
RA = Annotated[float, Field(ge=0, lt=360, description="Right ascension, degrees (ICRS)")]
DEC = Annotated[float, Field(ge=-90, le=90, description="Declination, degrees (ICRS)")]
Sectors = Annotated[list[int] | None, Field(description="Sectors to exclude (e.g. [93] for a QLP sector with artefacts)")]


async def _thread(fn, *a, **kw):
    return await anyio.to_thread.run_sync(lambda: fn(*a, **kw))


def _save_json(tic, name: str, obj: Any) -> str:
    p = target_dir(tic) / name
    p.write_text(dumps(obj), encoding="utf-8")
    return str(p)


def _load_json(tic, name: str, hint: str) -> Any:
    p = target_dir(tic) / name
    if not p.exists():
        raise FileNotFoundError(f"{p.name} not found for TIC {normalize_tic(tic)}: run {hint} first.")
    return json.loads(p.read_text(encoding="utf-8"))


def _public(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("_")}


# ================================================================ S1  Vetstar triage

@mcp.tool(name="ph_vetstar_status", annotations=READ_REMOTE)
async def ph_vetstar_status() -> str:
    """Check that the Vetstar API is reachable (free tier may take ~30 s to wake) and show its URL.

    Returns JSON {url, health} or an error string."""
    try:
        return dumps({"url": VETSTAR_URL, "health": await vetstar.health(),
                      "source": "https://github.com/eagnespuerto/vetstar"})
    except Exception as e:  # noqa: BLE001
        return error_text(e, f"Vetstar ({VETSTAR_URL})")


@mcp.tool(name="ph_vetstar_vet", annotations=WRITE_REMOTE)
async def ph_vetstar_vet(
    tic: TIC,
    sector: Annotated[int | None, Field(description="TESS sector; omit for Vetstar's multi-sector analysis")] = None,
    known_period_days: Annotated[float | None, Field(gt=0, le=1000, description="Restrict BLS to ±2% of this period and its P/2, 2P")] = None,
    high_variability: Annotated[bool, Field(description="Sinusoidal detrend before BLS (spotted rotators)")] = False,
    secondary_sigma: Annotated[float, Field(ge=1, le=7)] = 3.0,
    odd_even_sigma: Annotated[float, Field(ge=1, le=7)] = 3.0,
    detect_min_snr: Annotated[float, Field(gt=0, le=50)] = 4.0,
    save_pdf_report: Annotated[bool, Field(description="Also build and save Vetstar's PDF report locally")] = False,
) -> str:
    """Run the Vetstar transit/EB vetting pipeline on MAST data for a TIC (S1), then apply the
    spec's report-reliability red flags (BLS artefact periods, blind odd/even tests, placeholder depths,
    DV misfits, harmonics, R2 > 2 R_Jup, centroid offsets).

    Saves the full response to <workdir>/TIC<id>/vetstar_<sector|multi>.json.
    Returns JSON {summary, red_flags, saved, pdf_report?}. Can take minutes (MAST download server-side)."""
    t = normalize_tic(tic)
    opts = dict(known_period_days=known_period_days, high_variability=high_variability,
                secondary_sigma=secondary_sigma, odd_even_sigma=odd_even_sigma, detect_min_snr=detect_min_snr)
    try:
        res = await vetstar.analyze(t, sector, opts)
        saved = _save_json(t, f"vetstar_{sector if sector is not None else 'multi'}.json", res)
        norm = vetstar.normalise_api_result(res) if sector is not None else {
            **vetstar.normalise_api_result(res), "multisector": res.get("mast")}
        out = dict(summary=norm, red_flags=vetstar.red_flags(norm), saved=saved)
        if save_pdf_report:
            out["pdf_report"] = str(await vetstar.download_report(t, sector, opts))
        record_numbers(t, "vetstar", {k: v for k, v in norm.items() if not isinstance(v, (list, dict))})
        return dumps(out)
    except Exception as e:  # noqa: BLE001
        return error_text(e, "Vetstar")


@mcp.tool(name="ph_vetstar_parse_report", annotations=WRITE_LOCAL)
async def ph_vetstar_parse_report(
    pdf_path: Annotated[str, Field(description="Path to a Vetstar vetting PDF (single- or multi-sector)")],
    render_pages: Annotated[bool, Field(description="Also render pages to PNG (90 dpi) for visual inspection")] = False,
) -> str:
    """Extract every field from a Vetstar PDF with pymupdf (TIC, sector, Tmag, Teff, R★, CROWDSAP, dip table,
    BLS/LS periods, odd/even, secondary, centroid, physics, verdict) and run the S1 red-flag checks.

    Returns JSON {parsed, red_flags, page_images?}."""
    p = Path(pdf_path).expanduser()
    if not p.exists():
        return f"Error: {p} does not exist."
    try:
        parsed = await _thread(vetstar.parse_report, p)
        out = dict(parsed=parsed, red_flags=vetstar.red_flags(parsed))
        if render_pages:
            d = target_dir(parsed["tic"]) / "report_pages" if parsed.get("tic") else p.parent / f"{p.stem}_pages"
            out["page_images"] = await _thread(vetstar.render_pages, p, d)
        if parsed.get("tic"):
            record_numbers(parsed["tic"], "vetstar_report", {k: v for k, v in parsed.items()
                                                             if not isinstance(v, (list, dict))})
        return dumps(out)
    except Exception as e:  # noqa: BLE001
        return f"Error parsing {p.name}: {type(e).__name__}: {e}"


@mcp.tool(name="ph_quicklook_physics", annotations=READ_LOCAL)
async def ph_quicklook_physics(
    depth: Annotated[float, Field(gt=0, lt=1, description="Primary depth as a fraction (0.103 = 10.3%)")],
    rstar_rsun: Annotated[float, Field(gt=0, description="Host radius, R_sun (TIC 'rad')")],
    crowdsap: Annotated[float, Field(gt=0, le=1, description="SPOC CROWDSAP (fraction of aperture flux from target)")] = 1.0,
    secondary_depth: Annotated[float | None, Field(ge=0, lt=1, description="Secondary depth fraction, if seen")] = None,
    teff1: Annotated[float | None, Field(gt=1000, description="Primary Teff, K (needed for T2)")] = None,
) -> str:
    """S1 physics quick-look: R2 ≈ sqrt(depth/CROWDSAP)·R★ in R_sun/R_Jup/R_Earth, the >2 R_Jup 'not a planet'
    test, and (given a secondary) T2 from the surface-brightness ratio via Planck over 600–1000 nm."""
    try:
        return dumps(analysis.companion_quicklook(depth, rstar_rsun, crowdsap, secondary_depth, teff1))
    except ValueError as e:
        return f"Error: {e}"


# ================================================================ S2  identity + contamination

@mcp.tool(name="ph_resolve_target", annotations=READ_REMOTE)
async def ph_resolve_target(
    identifier: Annotated[str, Field(description="TIC ID ('TIC 402175877') or a star name ('HD 128329')")],
) -> str:
    """Resolve a target to its TIC v8.2 record at MAST: RA/Dec, Tmag, V, G, Teff, logg, R★, M★, distance,
    Gaia DR3 ID, contamination ratio. Returns JSON."""
    try:
        if identifier.strip().upper().startswith("TIC") or identifier.strip().isdigit():
            t = normalize_tic(identifier)
        else:
            t = (await _thread(catalogs.resolve_name, identifier))["TIC"]
        row = await _thread(catalogs.tic_lookup, t)
        record_numbers(t, "tic", row)
        return dumps(row)
    except Exception as e:  # noqa: BLE001
        return error_text(e, "MAST TIC")


@mcp.tool(name="ph_contamination", annotations=WRITE_REMOTE)
async def ph_contamination(
    tic: TIC,
    depth: Annotated[float, Field(gt=0, lt=1, description="Observed fractional dip depth on the target")],
    radius_arcsec: Annotated[float, Field(gt=0, le=600, description="Search radius (TESS pixels are 21″)")] = 150,
    centroid_sigma: Annotated[float | None, Field(description="Largest centroid offset in σ from the vetting report")] = None,
) -> str:
    """S2 contamination test with Gaia DR3 neighbours: for each, flux ratio f = 10^(−0.4ΔG) and the intrinsic
    dip it would need, δ_nb = δ(1+f)/f (>1 impossible; >0.5 implausible without a centroid shift).
    Combine with the centroid offset (< 3σ supports on-target). Records the verdict in paper_numbers.json."""
    t = normalize_tic(tic)
    try:
        star = await _thread(catalogs.tic_lookup, t)
        g_t = star.get("GAIAmag")
        cap = (g_t + 2.5 * np.log10(1 / depth) + 2.5) if g_t else None  # fainter sources cannot matter
        nbs = await _thread(catalogs.gaia_neighbours, star["ra"], star["dec"], radius_arcsec, cap)
        target = next((n for n in nbs if n["sep_arcsec"] < 2 and (g_t is None or abs(n["mag"] - g_t) < 0.5)), None)
        g_ref = target["mag"] if target else g_t
        others = [n for n in nbs if n is not target]
        csig, csrc = centroid_sigma, "argument"
        if csig is None:  # fall back to the larger Vetstar centroid offset recorded in the ledger
            led = load_numbers(t)
            v = led.get("vetstar") or led.get("vetstar_report") or {}
            sig = [abs(v[k]) for k in ("centroid_col_sigma", "centroid_row_sigma") if isinstance(v.get(k), (int, float))]
            csig, csrc = (max(sig), "vetstar") if sig else (None, None)
        res = analysis.contamination(depth, g_ref, others, centroid_sigma=csig)
        res["target_gaia"] = target
        res["centroid_source"] = csrc
        if csig is not None:
            res["centroid_on_target"] = abs(csig) < 3
        res["neighbours"] = res["neighbours"][:25]
        nb_min = next((n for n in res["neighbours"] if n["status"] != "excluded_by_centroid"), None)
        res["statement"] = res["verdict"] + (
            f"; the most capable remaining neighbour ({nb_min['name']}, {nb_min['sep_arcsec']:.0f}″) would need a "
            f"{nb_min['required_neighbour_dip']:.2f} intrinsic dip" if nb_min else "")
        record_numbers(t, "contamination", {k: v for k, v in res.items() if k != "neighbours"} |
                       {"closest_neighbours": res["neighbours"][:5]})
        return dumps(res)
    except Exception as e:  # noqa: BLE001
        return error_text(e, "Gaia DR3 / VizieR")


@mcp.tool(name="ph_simbad_region", annotations=READ_REMOTE)
async def ph_simbad_region(ra: RA, dec: DEC,
                           radius_arcsec: Annotated[float, Field(gt=0, le=600)] = 30) -> str:
    """SIMBAD cone search (main id, otype, spectral type, V, G, parallax, identifiers, and all historical
    otypes fetched separately to avoid duplicated rows). Use 30″ for identity and 150″ for the field."""
    try:
        return dumps(await _thread(catalogs.simbad_region, ra, dec, radius_arcsec))
    except Exception as e:  # noqa: BLE001
        return error_text(e, "SIMBAD")


# ================================================================ S3  literature / novelty

@mcp.tool(name="ph_simbad_references", annotations=READ_REMOTE)
async def ph_simbad_references(
    identifier: Annotated[str, Field(description="Exact SIMBAD identifier, e.g. 'HD 128329' or 'TIC 402175877'")],
    max_rows: Annotated[int, Field(ge=1, le=500)] = 100,
) -> str:
    """Bibcodes SIMBAD associates with an object (TAP join ref/has_ref/ident), newest first.
    SIMBAD is incomplete for survey catalogues: always run ph_vizier_sweep as well."""
    try:
        return dumps(await _thread(catalogs.simbad_references, identifier, max_rows))
    except Exception as e:  # noqa: BLE001
        return error_text(e, "SIMBAD TAP")


@mcp.tool(name="ph_vizier_sweep", annotations=READ_REMOTE)
async def ph_vizier_sweep(ra: RA, dec: DEC,
                          radius_arcsec: Annotated[float, Field(gt=0, le=30)] = 3) -> str:
    """S3 all-catalogue VizieR sweep: every table with a source within the radius, flagging those whose
    description matches var|binar|eclips|period|tess|kepler|asas|photom|light|rot|puls (with sample rows).
    This found IJspeert+2024's period for HD 128329 when SIMBAD had not. Read flagged tables in full next."""
    try:
        return dumps(await _thread(catalogs.vizier_sweep, ra, dec, radius_arcsec))
    except Exception as e:  # noqa: BLE001
        return error_text(e, "VizieR")


@mcp.tool(name="ph_vizier_query", annotations=READ_REMOTE)
async def ph_vizier_query(
    catalog: Annotated[str, Field(description="VizieR catalogue/table, e.g. 'B/vsx/vsx', 'I/355/gaiadr3', 'IV/39/tic82', 'J/A+A/691/A242'")],
    ra: RA, dec: DEC,
    radius_arcsec: Annotated[float, Field(gt=0, le=3600)] = 10,
    max_rows: Annotated[int, Field(ge=1, le=500)] = 50,
) -> str:
    """Cone query of one VizieR catalogue returning all columns plus separation _r (arcsec)."""
    try:
        return dumps(await _thread(catalogs.vizier_query, catalog, ra, dec, radius_arcsec, max_rows))
    except Exception as e:  # noqa: BLE001
        return error_text(e, "VizieR")


@mcp.tool(name="ph_exofop", annotations=READ_REMOTE)
async def ph_exofop(tic: TIC) -> str:
    """ExoFOP-TESS target record: TOIs, CTOIs, stellar/planet parameters, nearby targets."""
    try:
        return dumps(await catalogs.exofop_target(normalize_tic(tic)))
    except Exception as e:  # noqa: BLE001
        return error_text(e, "ExoFOP-TESS")


@mcp.tool(name="ph_exoplanet_archive_cone", annotations=READ_REMOTE)
async def ph_exoplanet_archive_cone(ra: RA, dec: DEC,
                                    radius_arcsec: Annotated[float, Field(gt=0, le=600)] = 30) -> str:
    """Known planets (pscomppars) and TESS Objects of Interest (toi) from the NASA Exoplanet Archive near a position."""
    try:
        return dumps(await catalogs.exoplanet_archive_cone(ra, dec, radius_arcsec))
    except Exception as e:  # noqa: BLE001
        return error_text(e, "NASA Exoplanet Archive")


@mcp.tool(name="ph_exoplanet_archive_query", annotations=READ_REMOTE)
async def ph_exoplanet_archive_query(
    adql: Annotated[str, Field(description="ADQL SELECT, e.g. \"select pl_name,pl_orbper from ps where tic_id='TIC 261136679'\"")],
    max_rows: Annotated[int, Field(ge=1, le=2000)] = 100,
) -> str:
    """Run an ADQL SELECT against the NASA Exoplanet Archive TAP service (tables ps, pscomppars, toi, ...)."""
    try:
        return dumps(await catalogs.exoplanet_archive_tap(adql, max_rows))
    except Exception as e:  # noqa: BLE001
        return error_text(e, "NASA Exoplanet Archive")


@mcp.tool(name="ph_ads_search", annotations=READ_REMOTE)
async def ph_ads_search(
    query: Annotated[str, Field(description="ADS query, e.g. 'object:\"HD 128329\"' or 'abs:\"TIC 402175877\"'")],
    rows: Annotated[int, Field(ge=1, le=100)] = 20,
) -> str:
    """NASA ADS search via the API (needs ADS_API_TOKEN). Without a token it explains how to proceed; it never
    scrapes the UI (which returns an HTTP 202 bot challenge that must not be bypassed)."""
    try:
        return dumps(await catalogs.ads_search(query, rows))
    except Exception as e:  # noqa: BLE001
        return error_text(e, "NASA ADS")


# ================================================================ S4  MAST light curves

@mcp.tool(name="ph_mast_list_lightcurves", annotations=READ_REMOTE)
async def ph_mast_list_lightcurves(tic: TIC) -> str:
    """List every TESS light-curve product at MAST (sector, author/pipeline, cadence) and which one per sector
    would be used (SPOC 2-min > TESS-SPOC FFI > QLP)."""
    t = normalize_tic(tic)
    try:
        rows = await _thread(lightcurves.search, t)
        best = lightcurves.choose_best(rows, list(lightcurves.AUTHOR_PREF))
        return dumps(dict(tic=t, products=rows, chosen_per_sector=list(best.values())))
    except Exception as e:  # noqa: BLE001
        return error_text(e, "MAST (lightkurve)")


@mcp.tool(name="ph_mast_download_lightcurves", annotations=WRITE_REMOTE)
async def ph_mast_download_lightcurves(
    tic: TIC,
    sectors: Annotated[list[int] | None, Field(description="Only these sectors (default: all)")] = None,
    authors: Annotated[list[Literal["SPOC", "TESS-SPOC", "QLP"]], Field(description="Allowed pipelines")] = ["SPOC", "TESS-SPOC", "QLP"],  # noqa: B006
) -> str:
    """S4: download (or reuse the local cache of) one normalised light curve per sector — quality_bitmask
    'default', PDCSAP if finite else flux, NaNs removed — and report the sector table (author, cadence,
    BTJD range) with warnings for artefact runs deeper than 50% (seen in QLP)."""
    t = normalize_tic(tic)
    try:
        table = await _thread(lightcurves.download, t, sectors, tuple(authors))
        record_numbers(t, "sectors", {"table": [{k: v for k, v in r.items() if k != "cache"} for r in table]})
        return dumps(dict(tic=t, workdir=str(target_dir(t)), sectors=table))
    except Exception as e:  # noqa: BLE001
        return error_text(e, "MAST (lightkurve)")


# ================================================================ S5–S7  analysis

@mcp.tool(name="ph_find_eclipses", annotations=WRITE_LOCAL)
async def ph_find_eclipses(
    tic: TIC, exclude_sectors: Sectors = None,
    dip_threshold: Annotated[float, Field(gt=0, lt=0.5, description="Fractional dip that counts as an eclipse")] = 0.03,
    max_depth: Annotated[float, Field(gt=0, le=1, description="Deeper events are rejected as artefacts")] = 0.5,
) -> str:
    """S5: find dips in 30-min bins of the cached light curves, fit a trapezoid (t0, depth, T14, T23) within
    ±0.6 d of each, and flag fully_covered events (data on both sides of ingress and egress).
    Saves eclipses.json. Only fully covered events should feed the ephemeris."""
    t = normalize_tic(tic)
    try:
        segs = lightcurves.load_segments(t, exclude_sectors)
        res = await _thread(analysis.find_eclipses, segs, dip_threshold, max_depth)
        _save_json(t, "eclipses.json", res)
        return dumps(res)
    except Exception as e:  # noqa: BLE001
        return f"Error: {type(e).__name__}: {e}"


@mcp.tool(name="ph_fit_ephemeris", annotations=WRITE_LOCAL)
async def ph_fit_ephemeris(
    tic: TIC, exclude_sectors: Sectors = None,
    trial_period: Annotated[float | None, Field(gt=0, description="Trial period in days (default: from separations)")] = None,
    dip_threshold: Annotated[float, Field(gt=0, lt=0.5)] = 0.03,
) -> str:
    """S6: weighted linear ephemeris T0 + P·E from fully covered eclipses (BTJD and BJD_TDB), O−C list, and
    alias tests: P/2 and P/3 are excluded if any predicted mid-time has data but no dip; 2P compares even vs
    odd epoch depths within the same pipeline. Reports the spec gate (≥3 events, >1 season, O−C ≪ duration)."""
    t = normalize_tic(tic)
    try:
        ev = [e for e in _load_json(t, "eclipses.json", "ph_find_eclipses")["events"]
              if not exclude_sectors or e["sector"] not in exclude_sectors]
        eph = analysis.fit_ephemeris(ev, trial_period)
        segs = lightcurves.load_segments(t, exclude_sectors)
        tt, ff = lightcurves.concat(segs)
        al = await _thread(analysis.alias_tests, tt, ff, eph["T0_btjd"], eph["P"], dip_threshold, 10, ev)
        eph["aliases"] = al
        eph["gate"]["period_consistent_with_data"] = al["P_consistent"]
        record_numbers(t, "ephemeris", eph)
        rows = [dict(epoch=e, sector=s, author=a, t_mid_btjd=x, t_mid_bjd_tdb=x + BTJD_OFFSET, oc_min=o, depth=d)
                for e, s, a, x, o, d in zip(eph["epochs"], eph["sectors"], eph["authors"], eph["times"],
                                            eph["oc_min"], eph["depths"])]
        _save_json(t, "eclipse_times.json", rows)
        return dumps(eph)
    except Exception as e:  # noqa: BLE001
        return f"Error: {type(e).__name__}: {e}"


async def _limb_darkening(t: str, tic_row: dict, u1: float | None, u2: float | None, sigma: float) -> dict:
    """LD prior for the batman fit: explicit u1/u2, else Claret (2017) TESS at the TIC Teff/logg/[M/H]."""
    if u1 is not None and u2 is not None:
        return dict(u1=u1, u2=u2, sigma=sigma, source="user")
    teff, logg = tic_row.get("Teff"), tic_row.get("logg")
    if not (teff and logg):
        return dict(analysis.LD_DEFAULT_PRIOR, note="TIC Teff/logg unknown; run ph_resolve_target for Claret LD")
    try:
        cl = await _thread(catalogs.claret2017_tess_ld, float(teff), float(logg), tic_row.get("MH"))
    except Exception as e:  # noqa: BLE001
        return dict(analysis.LD_DEFAULT_PRIOR, note=f"Claret (2017) lookup failed ({type(e).__name__}: {e})")
    record_numbers(t, "limb_darkening", cl)
    return dict(u1=u1 if u1 is not None else cl["u1"], u2=u2 if u2 is not None else cl["u2"], sigma=sigma,
                source=cl["source"], Teff=cl["Teff"], logg=cl["logg"], Z=cl["Z"])


@mcp.tool(name="ph_primary_stack", annotations=WRITE_LOCAL)
async def ph_primary_stack(
    tic: TIC, exclude_sectors: Sectors = None,
    m1_msun: Annotated[float | None, Field(gt=0, description="Primary mass (default TIC 'mass')")] = None,
    m2_msun: Annotated[float, Field(gt=0, description="Assumed companion mass for a/R1")] = 0.9,
    r1_rsun: Annotated[float | None, Field(gt=0, description="Primary radius (default TIC 'rad')")] = None,
    model: Annotated[Literal["trapezoid", "batman"], Field(
        description="'trapezoid' (default; eclipsing binaries) or 'batman': quadratic limb-darkened "
                    "Mandel–Agol transit for planet-sized companions")] = "trapezoid",
    u1: Annotated[float | None, Field(description="batman: quadratic LD u1 prior centre (default Claret 2017 TESS)")] = None,
    u2: Annotated[float | None, Field(description="batman: quadratic LD u2 prior centre (default Claret 2017 TESS)")] = None,
    ld_prior_sigma: Annotated[float, Field(ge=0, le=1, description="batman: Gaussian prior width on u1, u2 (0 fixes them)")] = 0.1,
) -> str:
    """S7 (primary): stack locally normalised primaries on the ephemeris, fit a trapezoid (depth, T14, T23),
    then the orbit geometry: k_min = sqrt(depth), R2_min, a/R1, circular central duration, implied b, and the
    e·sinω needed to hide a secondary. Requires ph_fit_ephemeris and ph_resolve_target (or explicit R1/M1).
    model='batman' fits a limb-darkened transit instead (Rp/R★, b, a/R★, mid-time, u1, u2 with priors from
    Claret 2017 via the TIC Teff/logg) and reports depth, T14, T23, b, Rp/R★ with uncertainties."""
    t = normalize_tic(tic)
    try:
        nums = load_numbers(t)
        eph = nums.get("ephemeris") or {}
        if not eph:
            return "Error: no ephemeris in paper_numbers.json; run ph_fit_ephemeris first."
        tic_row = nums.get("tic") or {}
        r1 = r1_rsun or tic_row.get("rad")
        m1 = m1_msun or tic_row.get("mass")
        if not (r1 and m1):
            return "Error: R1/M1 unknown; run ph_resolve_target or pass r1_rsun and m1_msun."
        segs = lightcurves.load_segments(t, exclude_sectors)
        if model == "batman":
            ld = await _limb_darkening(t, tic_row, u1, u2, ld_prior_sigma)
            st = await _thread(analysis.stack_primary, segs, eph["T0_btjd"], eph["P"], model="batman", ld=ld)
        else:
            st = await _thread(analysis.stack_primary, segs, eph["T0_btjd"], eph["P"])
        geo = analysis.orbit_geometry(st["depth"], st["T14_h"], eph["P"], r1, m1, m2_msun)
        np.savez_compressed(target_dir(t) / "primary_stack.npz",
                            **{f"{g}_{k}": v for g, d in st["_stack"].items() for k, v in d.items()},
                            params=np.array(st["_params"]), **({"model": np.array(model)} if model != "trapezoid" else {}))
        out = dict(primary=_public(st), geometry=geo, assumptions=dict(R1_rsun=r1, M1_msun=m1, M2_msun=m2_msun))
        record_numbers(t, "primary", _public(st) | {"n_eclipses_stacked": len(st["eclipses_used"])})
        record_numbers(t, "geometry", geo | out["assumptions"])
        return dumps(out)
    except Exception as e:  # noqa: BLE001
        return f"Error: {type(e).__name__}: {e}"


@mcp.tool(name="ph_secondary_search", annotations=WRITE_LOCAL)
async def ph_secondary_search(
    tic: TIC, exclude_sectors: Sectors = None,
    teff1: Annotated[float | None, Field(gt=1000, description="Primary Teff (default TIC)")] = None,
    teff2_grid: Annotated[list[int], Field(description="Companion Teffs for expected circular-orbit secondaries")] = [3000, 3500, 4000, 5000, 5500],  # noqa: B006
    phase_step: Annotated[float, Field(gt=0.0001, le=0.01)] = 0.001,
) -> str:
    """S7 (secondary): per-visit local-baseline search (±1.5 h window vs 2.5–6 h baseline, per orbit, weighted
    mean per trial phase), uncertainties inflated by the robust SNR scatter for red noise. Reports depth at
    phase 0.5, the maximum excursion and whether it is a lone spike, the 3σ limit, expected circular-orbit
    secondary depths (Planck, 600–1000 nm) and the companion Teff that would sit at the limit.
    Never uses a global baseline (sector offsets fake secondaries). Needs ph_primary_stack for the depth."""
    t = normalize_tic(tic)
    try:
        nums = load_numbers(t)
        eph, prim = nums.get("ephemeris"), nums.get("primary")
        if not eph:
            return "Error: run ph_fit_ephemeris first."
        segs = lightcurves.load_segments(t, exclude_sectors)
        tt, ff = lightcurves.concat(segs)
        sec = await _thread(analysis.secondary_search, tt, ff, eph["T0_btjd"], eph["P"], phase_step,
                            primary_T14_h=(prim or {}).get("T14_h"))
        np.savez_compressed(target_dir(t) / "secondary_curve.npz", **sec["_curve"])
        out = _public(sec)
        T1 = teff1 or (nums.get("tic") or {}).get("Teff")
        if prim and T1:
            exp = analysis.expected_secondary(prim["depth"], T1, teff2_grid)
            out["expected_circular_secondary"] = exp
            out["teff2_at_3sigma_limit"] = next((T2 for T2 in range(1500, int(T1), 25)
                                                 if prim["depth"] * analysis.band_flux(T2) / analysis.band_flux(T1)
                                                 > sec["limit_3sigma"]), None)
            out["teff1_used"] = T1
        record_numbers(t, "secondary", out)
        return dumps(out)
    except Exception as e:  # noqa: BLE001
        return f"Error: {type(e).__name__}: {e}"


@mcp.tool(name="ph_make_figure", annotations=WRITE_LOCAL)
async def ph_make_figure(
    tic: TIC,
    out_dir: Annotated[str | None, Field(description="Directory for the figure (default <workdir>/TIC<id>/paper)")] = None,
    job: Annotated[str | None, Field(description="LaTeX job name; the figure is <job>_fig1.pdf so it never equals the job name")] = None,
    reference_teff2: Annotated[int | None, Field(description="Companion Teff for the expected-secondary line")] = None,
) -> str:
    """S8: the single RNAAS figure — (a) stacked primary with the fitted model (trapezoid or limb-darkened), (b) secondary search with ±3σ band
    and the expected circular-orbit depth (off-scale values go in a label). Writes PDF and PNG."""
    t = normalize_tic(tic)
    try:
        d = Path(out_dir).expanduser() if out_dir else target_dir(t) / "paper"
        d.mkdir(parents=True, exist_ok=True)
        jb = job or f"tic{t}_rnaas"
        z = np.load(target_dir(t) / "primary_stack.npz")
        stack = {g: dict(x=z[f"{g}_x"], f=z[f"{g}_f"]) for g in ("2min", "ffi")}
        scp = target_dir(t) / "secondary_curve.npz"
        sec = dict(np.load(scp)) if scp.exists() else None
        nums = load_numbers(t)
        exp = {int(k): v for k, v in (nums.get("secondary", {}).get("expected_circular_secondary") or {}).items()}
        r2 = (nums.get("geometry") or {}).get("R2_min_rsun")
        if r2 is not None and r2 * analysis.RSUN_RJUP <= 2.0 and reference_teff2 is None:
            exp = {}  # planet-sized companion: a stellar-companion secondary line would mislead
        model = str(z["model"]) if "model" in z.files else "trapezoid"
        files = await _thread(rnaas_figure, d / f"{jb}_fig1", stack, list(z["params"]), nums["ephemeris"]["P"],
                              sec, exp or None, reference_teff2, model)
        return dumps(dict(files=files, next_step="Inspect the PNG before using it in the manuscript."))
    except FileNotFoundError as e:
        return f"Error: {e}. Run ph_primary_stack (and ph_secondary_search) first."
    except Exception as e:  # noqa: BLE001
        return f"Error: {type(e).__name__}: {e}"


@mcp.tool(name="ph_paper_numbers", annotations=READ_LOCAL)
async def ph_paper_numbers(tic: TIC) -> str:
    """Return the paper_numbers.json ledger for a target: every number a manuscript may quote, by section
    (tic, vetstar, contamination, sectors, ephemeris, primary, geometry, secondary)."""
    t = normalize_tic(tic)
    return dumps(dict(path=str(target_dir(t) / "paper_numbers.json"), numbers=load_numbers(t)))


# ================================================================ S9–S12  manuscript + release

@mcp.tool(name="ph_verify_references", annotations=READ_REMOTE)
async def ph_verify_references(
    dois: Annotated[list[str] | None, Field(description="DOIs to check (journal, dataset, software, Zenodo)")] = None,
    bib_path: Annotated[str | None, Field(description="A .bib file: every entry with a doi field is checked against its title/volume/pages")] = None,
    tex_path: Annotated[str | None, Field(description="A .tex file: every \\dataset[doi:...] is checked")] = None,
) -> str:
    """S10: resolve every DOI at Crossref (journals) or DataCite (data/software) and compare title, volume and
    page. A DOI written from memory resolved to a different paper in the reference run — never skip this."""
    import re
    items: list[dict] = [dict(doi=d) for d in (dois or [])]
    if bib_path:
        for ent in references.parse_bib(Path(bib_path).expanduser().read_text(encoding="utf-8")):
            f = ent["fields"]
            if f.get("doi"):
                items.append(dict(doi=f["doi"], key=ent["key"], title=f.get("title"), volume=f.get("volume"),
                                  page=(f.get("pages") or "").split("-")[0] or None))
            else:
                items.append(dict(key=ent["key"], missing_doi=True))
    if tex_path:
        src = Path(tex_path).expanduser().read_text(encoding="utf-8")
        items += [dict(doi=d, key="\\dataset") for d in re.findall(r"\\dataset\[doi:([^\]]+)\]", src)]
    results = []
    for it in items:
        if it.get("missing_doi"):
            results.append(dict(key=it["key"], ok=None, message="no DOI in bib entry; verify manually"))
            continue
        try:
            r = await references.verify_doi(it["doi"], it.get("title"), it.get("volume"), it.get("page"))
            results.append({"key": it.get("key"), **r})
        except Exception as e:  # noqa: BLE001
            results.append(dict(key=it.get("key"), doi=it["doi"], ok=False, error=error_text(e, "Crossref/DataCite")))
    return dumps(dict(n=len(results), all_ok=all(r.get("ok") is not False for r in results), results=results))


@mcp.tool(name="ph_uat_search", annotations=READ_REMOTE)
async def ph_uat_search(
    term: Annotated[str | None, Field(description="Keyword to look up, e.g. 'eclipsing binary'")] = None,
    verify_ids: Annotated[list[int] | None, Field(description="UAT concept IDs to verify, e.g. [444, 918]")] = None,
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
) -> str:
    """Unified Astronomy Thesaurus lookup/verification from the official UAT.json (name, ID, hierarchy path,
    exoplanet-branch flag). Use verified IDs in \\keywords{\\uat{Name}{ID}}."""
    try:
        out: dict = {}
        if term:
            out["search"] = await references.uat_search(term, limit)
        if verify_ids:
            out["verify"] = await references.uat_verify(verify_ids)
        return dumps(out or {"error": "pass term and/or verify_ids"})
    except Exception as e:  # noqa: BLE001
        return error_text(e, "UAT")


@mcp.tool(name="ph_rnaas_scaffold", annotations=WRITE_LOCAL)
async def ph_rnaas_scaffold(
    tic: TIC,
    title: str,
    author: Annotated[str, Field(description="Author name as it should print")],
    affiliation: Annotated[str, Field(description="e.g. 'Independent Researcher, City, Country'")],
    email: str,
    orcid: Annotated[str | None, Field(description="ORCID iD, e.g. 0000-0000-0000-0000")] = None,
    star_label: Annotated[str | None, Field(description="Name used in the text, e.g. 'HD 128329'")] = None,
    keywords: Annotated[list[tuple[str, int]] | None, Field(description="Verified UAT (name, id) pairs")] = None,
    job: Annotated[str | None, Field(description="LaTeX job name (default tic<id>_rnaas)")] = None,
    vetstar_version: str = "",
    ai_tool: Annotated[str, Field(description="AI tool and model for the AI-disclosure paragraph")] = "Claude (Anthropic), via Claude Code",
    out_dir: str | None = None,
    overwrite: bool = False,
) -> str:
    """S9: write an AASTeX 7.0.1 Research Note skeleton (twocolumn preprint form) with author block, RNAAS
    journal tag, figure* placed at the start of Section 2, acknowledgments boilerplate, AI disclosure,
    \\facilities, \\software crediting Vetstar, and \\dataset entries for every MAST pipeline used plus TIC.
    Prose is left as %% TODO blocks; the paper_numbers.json ledger is embedded as comments."""
    t = normalize_tic(tic)
    d = Path(out_dir).expanduser() if out_dir else target_dir(t) / "paper"
    jb = job or f"tic{t}_rnaas"
    if (d / f"{jb}.tex").exists() and not overwrite:
        return f"Error: {d / f'{jb}.tex'} exists; pass overwrite=true to replace it."
    nums = load_numbers(t)
    used = sorted({r["author"] for r in (nums.get("sectors", {}).get("table") or []) if not r.get("error")})
    res = manuscript.scaffold_rnaas(d, jb, title=title, author=author, orcid=orcid, affiliation=affiliation,
                                    email=email, star_label=star_label or f"TIC {t}", keywords=keywords or [],
                                    pipelines=used or ["SPOC"], numbers=nums, vetstar_version=vetstar_version,
                                    ai_tool=ai_tool)
    return dumps(res)


@mcp.tool(name="ph_rnaas_check", annotations=READ_LOCAL)
async def ph_rnaas_check(tex_path: str) -> str:
    """Check a manuscript against RNAAS limits and the spec: abstract ≤150 words, body ≤1000, ≤1 figure or
    table, ≤15 references, \\email, AI disclosure, \\facilities, \\software, \\dataset, originating-tool credit,
    figure filename ≠ job name, figure* placed early, no TODO left."""
    p = Path(tex_path).expanduser()
    if not p.exists():
        return f"Error: {p} does not exist."
    return dumps(manuscript.check_rnaas(p))


@mcp.tool(name="ph_latex_build", annotations=WRITE_LOCAL)
async def ph_latex_build(tex_path: str, use_bibtex: bool = True) -> str:
    """Compile with pdfLaTeX → BibTeX → pdfLaTeX ×2, fail on '^! ' or 'undefined' in the log, and render
    every page to PNG for visual inspection. Needs TeX Live with aastex701.cls and aasjournalv7.bst."""
    p = Path(tex_path).expanduser()
    if not p.exists():
        return f"Error: {p} does not exist."
    return dumps(await _thread(manuscript.latex_build, p, use_bibtex))


@mcp.tool(name="ph_rnaas_submission_folder", annotations=REPLACE_LOCAL)
async def ph_rnaas_submission_folder(
    tex_path: str,
    out_dir: Annotated[str | None, Field(description="Default: <tex dir>/submission (replaced if it exists)")] = None,
    single_column: Annotated[bool, Field(description="RNAAS asked for single column without line numbers")] = True,
) -> str:
    """S11: build the flat RNAAS upload folder (.tex, .bib, .bbl, figure, compiled .pdf) and prove it compiles
    standalone in a clean directory both with the shipped .bbl and with fresh BibTeX. Does not submit."""
    p = Path(tex_path).expanduser()
    if not p.exists():
        return f"Error: {p} does not exist."
    return dumps(await _thread(manuscript.build_submission, p, Path(out_dir).expanduser() if out_dir else None,
                               single_column))


@mcp.tool(name="ph_zenodo_prepare_bundle", annotations=WRITE_LOCAL)
async def ph_zenodo_prepare_bundle(
    tic: TIC,
    tex_path: str,
    title: str,
    creators: Annotated[list[dict], Field(description="[{'name': 'Family, Given', 'affiliation': '...', 'orcid': '...'}]")],
    description: str,
    keywords: list[str],
    file_stem: Annotated[str, Field(description="Prefix for uploaded files, e.g. 'Puerto_2026_HD128329_RNAAS'")],
    reference_lines: Annotated[list[str], Field(description="One formatted citation per line for Zenodo's References field")],
    cited_dois: Annotated[list[str] | None, Field(description="Verified DOIs of cited papers/software (relation 'references')")] = None,
    code_files: Annotated[list[str] | None, Field(description="Extra scripts/data to include in the code+data zip")] = None,
    journal: str = "RNAAS",
    out_dir: str | None = None,
) -> str:
    """S12: prepare (never upload) the Zenodo preprint bundle: preprint PDF, LaTeX source zip, code+data zip
    (incl. paper_numbers.json and eclipse times), timing CSV, plus zenodo_metadata.json (deposit API format,
    'Preprint submitted to <journal>; not yet peer reviewed', CC BY 4.0, Journal blank, version 'v1 (submitted
    to <journal>)', datasets as isDerivedFrom), ZENODO_UPLOAD.md walkthrough and zenodo_references.txt."""
    t = normalize_tic(tic)
    try:
        tp = Path(tex_path).expanduser()
        td = target_dir(t)
        nums = load_numbers(t)
        used = sorted({r["author"] for r in (nums.get("sectors", {}).get("table") or []) if not r.get("error")})
        dsets = [MAST_DATASET_DOIS[p] for p in dict.fromkeys(used + ["TIC"]) if p in MAST_DATASET_DOIS]
        files = [Path(f).expanduser() for f in (code_files or [])]
        files += [td / n for n in ("paper_numbers.json", "eclipses.json", "eclipse_times.json") if (td / n).exists()]
        rows = json.loads((td / "eclipse_times.json").read_text(encoding="utf-8")) if (td / "eclipse_times.json").exists() else None
        res = zenodo.build_bundle(tex_path=tp, code_files=files, out_dir=Path(out_dir).expanduser() if out_dir else tp.parent / "zenodo",
                                  file_stem=file_stem, title=title, creators=creators, description=description,
                                  keywords=keywords, journal=journal, dataset_dois=dsets,
                                  reference_lines=reference_lines, eclipse_rows=rows, cited_dois=cited_dois)
        res["reminder"] = "Prepared only. The human uploads and clicks Publish; then run ph_zenodo_check_record."
        return dumps(res)
    except Exception as e:  # noqa: BLE001
        return f"Error: {type(e).__name__}: {e}"


@mcp.tool(name="ph_zenodo_check_record", annotations=READ_REMOTE)
async def ph_zenodo_check_record(
    record: Annotated[str, Field(description="Zenodo record ID, URL or DOI (10.5281/zenodo.N)")],
    plan_path: Annotated[str | None, Field(description="zenodo_metadata.json to diff against")] = None,
) -> str:
    """After the human publishes: fetch the public Zenodo record, report version DOI and concept DOI, and diff
    creators, version, related identifiers and journal field against the planned metadata."""
    try:
        return dumps(await zenodo.check_record(record, Path(plan_path).expanduser() if plan_path else None))
    except Exception as e:  # noqa: BLE001
        return error_text(e, "Zenodo")


@mcp.tool(name="ph_acceptance_checklist", annotations=READ_LOCAL)
async def ph_acceptance_checklist(
    tic: TIC,
    tex_path: Annotated[str | None, Field(description="Manuscript to check (optional)")] = None,
) -> str:
    """Spec §3 acceptance checklist, evaluated from the ledger and manuscript: pass / fail / not-run per item
    with the evidence found. DOI verification and visual page inspection must be confirmed by the agent."""
    t = normalize_tic(tic)
    n = load_numbers(t)
    eph, sec, con = n.get("ephemeris", {}), n.get("secondary", {}), n.get("contamination", {})
    al = eph.get("aliases", {})
    items = {
        "verdict_with_contamination_numbers_and_centroid": (
            ("pass" if con.get("centroid_sigma") is not None else "partial: no centroid σ recorded") if con else "not run",
            con.get("statement")),
        "prior_literature_table": ("agent must confirm",
                                   "ph_vizier_sweep + ph_simbad_references + ph_exofop + ph_ads_search, written up"),
        "ephemeris_aliases_2P": (
            ("pass" if eph.get("gate", {}).get("ge3_events") and al.get("P_consistent")
             and al.get("all_shorter_aliases_excluded") and al.get("2P")
             else "fail") if eph else "not run",
            {"P": eph.get("P"), "e_P": eph.get("e_P"), "gate": eph.get("gate"),
             "P/2": al.get("P/2"), "P/3": al.get("P/3"), "2P": al.get("2P")} if eph else None),
        "secondary_limit_inflation_spike": (
            "pass" if sec.get("limit_3sigma") is not None else "not run",
            {k: sec.get(k) for k in ("depth_at_0p5", "err_at_0p5", "limit_3sigma", "inflation_factor", "lone_spike")} if sec else None),
        "numbers_in_ledger": ("pass" if n else "not run", str(target_dir(t) / "paper_numbers.json")),
        "dois_verified": ("agent must confirm", "run ph_verify_references on the .bib and .tex"),
        "nothing_published_by_agent": ("pass", "this server has no upload/submit/publish tools"),
    }
    if tex_path and Path(tex_path).expanduser().exists():
        chk = manuscript.check_rnaas(Path(tex_path).expanduser())
        items["latex_limits"] = ("pass" if chk["passed"] else "fail", {"failed": chk["failed"],
                                 "abstract_words": chk["abstract_words"], "body_words": chk["body_words"]})
        items["ai_disclosure_and_tool_credit"] = (
            "pass" if chk["checks"]["ai_disclosure_present"] and chk["checks"]["originating_tool_credited"] else "fail", None)
    return dumps({k: {"status": s, "evidence": ev} for k, (s, ev) in items.items()})


# ================================================================ resources + prompt

@mcp.resource("planet-hunter://spec", name="agent_spec", mime_type="text/markdown",
              description="AGENT_SPEC: the TESS EB candidate → ephemeris → RNAAS + Zenodo pipeline contract")
def spec_resource() -> str:
    return resources.files("planet_hunter_mcp").joinpath("AGENT_SPEC.md").read_text(encoding="utf-8")


@mcp.resource("planet-hunter://targets/{tic}/numbers", name="paper_numbers", mime_type="application/json",
              description="paper_numbers.json ledger for a target")
def numbers_resource(tic: str) -> str:
    return dumps(load_numbers(tic))


@mcp.prompt(name="vet_tess_candidate", description="Walk the full S1–S12 pipeline for one TESS candidate")
def vet_tess_candidate(tic: str, report_pdf: str = "") -> str:
    src = f"the Vetstar report at {report_pdf}" if report_pdf else "a fresh ph_vetstar_vet run"
    return (f"Vet TIC {tic} starting from {src}. Follow planet-hunter://spec stage by stage (S1–S12). "
            "At each stage state Do → Decide → Gate, quote numbers only from ph_paper_numbers, run the literature "
            "sweep before any novelty claim, verify every DOI and UAT ID, and finish with ph_acceptance_checklist. "
            "Prepare files only: never submit, upload, publish or push.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Planet Hunter MCP server")
    ap.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    WORKDIR.mkdir(parents=True, exist_ok=True)
    if a.transport == "stdio":
        mcp.run()
    else:
        mcp.run("streamable-http", host=a.host, port=a.port, stateless_http=True, json_response=True)


if __name__ == "__main__":
    main()
