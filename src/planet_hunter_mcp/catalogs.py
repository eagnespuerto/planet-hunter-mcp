"""Catalogue identity, contamination and literature lookups (spec S2–S3).

Services: MAST TIC, SIMBAD (astroquery + TAP), VizieR (named catalogues and the all-catalogue
sweep), Gaia DR3, ExoFOP-TESS, NASA Exoplanet Archive (TAP) and NASA ADS (token required).
astroquery calls are synchronous; server.py runs them in worker threads.
"""
from __future__ import annotations

import re
import warnings

import numpy as np

from .config import ADS_API_TOKEN
from .util import http_get, http_post

warnings.filterwarnings("ignore")

VARIABILITY_KEYWORDS = re.compile(
    r"var|binar|eclips|period|tess|kepler|asas|photom|light|rot|puls", re.I)

CONTAM_CATALOGS = {"vsx": "B/vsx/vsx", "gaia_dr3": "I/355/gaiadr3", "tic82": "IV/39/tic82"}


def _coord(ra: float, dec: float):
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    return SkyCoord(ra * u.deg, dec * u.deg)


def _table_rows(tab, max_rows: int = 50) -> list[dict]:
    rows = []
    for r in tab[:max_rows]:
        d = {}
        for c in tab.colnames:
            v = r[c]
            if np.ma.is_masked(v):
                v = None
            elif isinstance(v, bytes):
                v = v.decode()
            elif hasattr(v, "item"):
                v = v.item()
            d[c] = v
        rows.append(d)
    return rows


# --------------------------------------------------------------------------- TIC

def tic_lookup(tic: int) -> dict:
    """TIC v8.2 row from MAST: coordinates, Tmag, Teff, R★, M★, logg, Gaia ID, contamination ratio."""
    from astroquery.mast import Catalogs
    tab = Catalogs.query_criteria(catalog="Tic", ID=tic)
    if len(tab) == 0:
        raise ValueError(f"TIC {tic} not found at MAST.")
    r = _table_rows(tab, 1)[0]
    keep = ["ID", "ra", "dec", "Tmag", "Vmag", "GAIAmag", "Teff", "e_Teff", "logg", "rad", "e_rad",
            "mass", "e_mass", "d", "e_d", "plx", "GAIA", "HIP", "TYC", "contratio", "lumclass", "objType"]
    return {k: r.get(k) for k in keep if k in r}


def resolve_name(name: str) -> dict:
    """Resolve a star name (e.g. 'HD 128329') to coordinates and its TIC ID via MAST."""
    from astroquery.mast import Catalogs
    tab = Catalogs.query_object(name, catalog="Tic", radius=0.002)
    if len(tab) == 0:
        raise ValueError(f"MAST could not resolve {name!r}.")
    tab.sort("dstArcSec")
    r = _table_rows(tab, 1)[0]
    return dict(name=name, TIC=int(r["ID"]), ra=r["ra"], dec=r["dec"], Tmag=r.get("Tmag"),
                separation_arcsec=r.get("dstArcSec"))


# ------------------------------------------------------------------------ SIMBAD

def simbad_region(ra: float, dec: float, radius_arcsec: float = 30, max_rows: int = 50) -> dict:
    """SIMBAD cone: main id, otype, spectral type, V, G, parallax, all identifiers.

    otypes (all historical object types) are fetched in a separate query — joining them into
    the main query duplicates rows (spec S2)."""
    import astropy.units as u
    from astroquery.simbad import Simbad
    s = Simbad()
    s.ROW_LIMIT = max_rows
    s.add_votable_fields("otype", "sp_type", "V", "G", "plx_value", "ids")
    tab = s.query_region(_coord(ra, dec), radius=radius_arcsec * u.arcsec)
    rows = _table_rows(tab, max_rows) if tab is not None else []
    for r in rows:
        if r.get("ids"):
            r["ids"] = r["ids"].split("|")
    if rows:
        s2 = Simbad()
        s2.add_votable_fields("otypes")
        try:
            t2 = s2.query_objects([r["main_id"] for r in rows])
            ot: dict[str, set] = {}
            for x in _table_rows(t2, 10_000):
                key = x.get("main_id") or x.get("user_specified_id")
                val = x.get("otypes.otype") or x.get("otypes")
                if key and val:
                    ot.setdefault(key, set()).add(val)
            for r in rows:
                r["otypes"] = sorted(ot.get(r["main_id"], []))
        except Exception as e:  # noqa: BLE001
            for r in rows:
                r["otypes_error"] = str(e)
    return dict(ra=ra, dec=dec, radius_arcsec=radius_arcsec, n=len(rows), objects=rows)


def simbad_references(identifier: str, max_rows: int = 100) -> dict:
    """Bibcodes SIMBAD links to an object, via TAP (ref ⋈ has_ref ⋈ ident)."""
    from astroquery.simbad import Simbad
    ident = identifier.replace("'", "''")
    adql = (f"SELECT TOP {int(max_rows)} b.bibcode, b.\"year\" AS pub_year, b.journal, b.title "
            "FROM ref AS b JOIN has_ref AS h ON b.oidbib = h.oidbibref "
            f"JOIN ident AS i ON i.oidref = h.oidref WHERE i.id = '{ident}' ORDER BY pub_year DESC")
    tab = Simbad.query_tap(adql)
    rows = _table_rows(tab, max_rows)
    return dict(identifier=identifier, n=len(rows), references=rows,
                caveat="SIMBAD is incomplete for photometric surveys; always run ph_vizier_sweep too.")


# ------------------------------------------------------------------------ VizieR

def vizier_query(catalog: str, ra: float, dec: float, radius_arcsec: float, max_rows: int = 50) -> dict:
    import astropy.units as u
    from astroquery.vizier import Vizier
    v = Vizier(columns=["**", "+_r"], row_limit=max_rows)
    res = v.query_region(_coord(ra, dec), radius=radius_arcsec * u.arcsec, catalog=catalog)
    tables = {}
    for name in res.keys():
        tables[name] = _table_rows(res[name], max_rows)
    return dict(catalog=catalog, radius_arcsec=radius_arcsec, tables=tables)


def vizier_sweep(ra: float, dec: float, radius_arcsec: float = 3, rows_per_table: int = 3) -> dict:
    """Every VizieR table containing a source within `radius_arcsec`; flag variability/period tables.

    This is the step that found IJspeert+2024's period for HD 128329 when SIMBAD had missed it."""
    import astropy.units as u
    from astroquery.vizier import Vizier
    v = Vizier(columns=["**", "+_r"], row_limit=rows_per_table)
    res = v.query_region(_coord(ra, dec), radius=radius_arcsec * u.arcsec)
    flagged, other = [], []
    for name in res.keys():
        tab = res[name]
        desc = (tab.meta.get("description") or "")
        entry = dict(table=name, description=desc, n_rows=len(tab))
        if VARIABILITY_KEYWORDS.search(desc) or VARIABILITY_KEYWORDS.search(name):
            entry["rows"] = _table_rows(tab, rows_per_table)
            flagged.append(entry)
        else:
            other.append(entry)
    return dict(ra=ra, dec=dec, radius_arcsec=radius_arcsec, n_tables=len(flagged) + len(other),
                flagged=flagged, other=other,
                next_step="Read every flagged table in full (ph_vizier_query) and record prior periods/epochs/e.")


def gaia_neighbours(ra: float, dec: float, radius_arcsec: float = 63, max_gmag: float | None = None,
                    max_rows: int = 500) -> list[dict]:
    """Gaia DR3 sources within radius (separation in arcsec, sorted), optionally brighter than max_gmag.

    Gaia positions are epoch 2016; the target itself is normally the brightest source within ~1″."""
    import astropy.units as u
    from astroquery.vizier import Vizier
    filt = {"Gmag": f"<{max_gmag}"} if max_gmag is not None else {}
    v = Vizier(columns=["Source", "RA_ICRS", "DE_ICRS", "Gmag", "BP-RP", "Plx", "+_r"],
               column_filters=filt, row_limit=max_rows)
    res = v.query_region(_coord(ra, dec), radius=radius_arcsec * u.arcsec, catalog="I/355/gaiadr3")
    if not res:
        return []
    rows = []
    for r in _table_rows(res[0], max_rows):
        if r.get("Gmag") is None:
            continue
        rows.append(dict(name=f"Gaia DR3 {r['Source']}", mag=float(r["Gmag"]), sep_arcsec=float(r["_r"]),
                         bp_rp=r.get("BP-RP"), plx=r.get("Plx")))
    rows.sort(key=lambda x: x["sep_arcsec"])
    return rows


# ---------------------------------------------------------- ExoFOP / Exoplanet Archive

async def exofop_target(tic: int) -> dict:
    r = await http_get("https://exofop.ipac.caltech.edu/tess/target.php", params={"id": tic, "json": ""})
    data = r.json()
    summary = {k: data.get(k) for k in ("basic_info", "tois", "ctois", "planet_parameters",
                                        "stellar_parameters", "nearby_targets") if k in data}
    summary["toi_count"] = len(data.get("tois") or [])
    summary["ctoi_count"] = len(data.get("ctois") or [])
    summary["source"] = f"https://exofop.ipac.caltech.edu/tess/target.php?id={tic}"
    return summary


NEA_TAP = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"


async def exoplanet_archive_tap(adql: str, max_rows: int = 100) -> dict:
    """Run ADQL against the NASA Exoplanet Archive (tables: ps, pscomppars, toi, keplernames, ...)."""
    q = adql.strip().rstrip(";")
    if not re.match(r"(?is)^select\b", q):
        raise ValueError("Only SELECT queries are allowed.")
    if not re.search(r"(?i)\btop\s+\d+", q):
        q = re.sub(r"(?i)^select\s+", f"SELECT TOP {int(max_rows)} ", q, count=1)
    # POST: the archive's firewall returns 403 for ADQL geometry functions sent as GET parameters.
    r = await http_post(NEA_TAP, data={"query": q, "format": "json"})
    rows = r.json()
    return dict(query=q, n=len(rows), rows=rows[:max_rows])


async def exoplanet_archive_cone(ra: float, dec: float, radius_arcsec: float = 30) -> dict:
    """Known planets (pscomppars) and TOIs within a cone."""
    rdeg = radius_arcsec / 3600
    planets = await exoplanet_archive_tap(
        "SELECT pl_name, hostname, tic_id, pl_orbper, pl_rade, pl_bmasse, disc_method, disc_year, ra, dec "
        f"FROM pscomppars WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra}, {dec}, {rdeg})) = 1")
    # The toi table has no spatial index (CONTAINS fails with ORA-00904), so use an RA/Dec box
    # and trim by true separation.
    dra = min(rdeg / max(np.cos(np.radians(dec)), 1e-3), 180)
    tois = await exoplanet_archive_tap(
        "SELECT toi, tid, tfopwg_disp, pl_orbper, pl_tranmid, pl_trandep, pl_trandurh, pl_rade, ra, dec "
        f"FROM toi WHERE ra BETWEEN {ra - dra} AND {ra + dra} AND dec BETWEEN {dec - rdeg} AND {dec + rdeg}")
    c0 = _coord(ra, dec)
    tois["rows"] = [r for r in tois["rows"]
                    if c0.separation(_coord(r["ra"], r["dec"])).arcsec <= radius_arcsec]
    return dict(radius_arcsec=radius_arcsec, planets=planets["rows"], tois=tois["rows"])


# ----------------------------------------------------------------------------- ADS

async def ads_search(query: str, rows: int = 20) -> dict:
    if not ADS_API_TOKEN:
        return dict(error="ADS_API_TOKEN is not set. Scripted access to the ADS web UI returns an HTTP 202 "
                          "bot challenge that must not be bypassed: ask the user to set the token "
                          "(https://ui.adsabs.harvard.edu/user/settings/token) or run this query themselves.",
                    query=query)
    r = await http_get("https://api.adsabs.harvard.edu/v1/search/query",
                       params={"q": query, "rows": rows, "fl": "bibcode,title,author,year,doi,pub,citation_count",
                               "sort": "date desc"},
                       headers={"Authorization": f"Bearer {ADS_API_TOKEN}"})
    docs = r.json().get("response", {}).get("docs", [])
    for d in docs:
        if isinstance(d.get("title"), list):
            d["title"] = d["title"][0]
        if isinstance(d.get("author"), list):
            d["author"] = d["author"][:3] + (["et al."] if len(d["author"]) > 3 else [])
    return dict(query=query, n=len(docs), docs=docs)
