"""MAST light-curve search, download and local caching via lightkurve (spec S4)."""
from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np

from .analysis import Segment
from .config import target_dir

AUTHOR_PREF = {"SPOC": 0, "TESS-SPOC": 1, "QLP": 2}


def _lk():
    warnings.filterwarnings("ignore")
    import lightkurve as lk  # heavy import, deferred
    return lk


def search(tic: int) -> list[dict]:
    """Every TESS light-curve product at MAST for the target."""
    lk = _lk()
    sr = lk.search_lightcurve(f"TIC {tic}", mission="TESS")
    rows = []
    for i, row in enumerate(sr.table):
        try:
            sec = int(str(sr.mission[i]).split()[-1])
        except ValueError:
            continue
        rows.append(dict(index=i, sector=sec, author=str(row["author"]), exptime_s=float(row["exptime"]),
                         year=int(row["year"]) if "year" in row.colnames else None))
    return rows


def _rank(author: str, exptime: float) -> tuple:
    """SPOC 2-min > TESS-SPOC FFI > QLP. Within a pipeline 120 s beats 20 s (the 20-s products are
    ~6× larger and add nothing for eclipse timing), then the shortest cadence wins."""
    return (AUTHOR_PREF[author], 0 if abs(exptime - 120) < 1 else 1, exptime)


def choose_best(rows: list[dict], authors: list[str]) -> dict[int, dict]:
    """Pick one product per sector using _rank."""
    best: dict[int, dict] = {}
    for r in rows:
        if r["author"] not in authors or r["author"] not in AUTHOR_PREF:
            continue
        key = _rank(r["author"], r["exptime_s"])
        if r["sector"] not in best or key < best[r["sector"]]["_key"]:
            best[r["sector"]] = {**r, "_key": key}
    return {s: {k: v for k, v in r.items() if k != "_key"} for s, r in sorted(best.items())}


def _cache_path(tic: int, sector: int, author: str, exptime: float) -> Path:
    d = target_dir(tic) / "lc"
    d.mkdir(exist_ok=True)
    return d / f"S{sector:03d}_{author}_{int(exptime)}s.npz"


def download(tic: int, sectors: list[int] | None = None,
             authors: list[str] = ("SPOC", "TESS-SPOC", "QLP"), max_depth: float = 0.5) -> list[dict]:
    """Download (or reuse cached) normalised PDCSAP/flux arrays, one product per sector."""
    lk = _lk()
    sr = lk.search_lightcurve(f"TIC {tic}", mission="TESS")
    rows = []
    for i, row in enumerate(sr.table):
        try:
            sec = int(str(sr.mission[i]).split()[-1])
        except ValueError:
            continue
        rows.append(dict(index=i, sector=sec, author=str(row["author"]), exptime_s=float(row["exptime"])))
    best = choose_best(rows, list(authors))
    out = []
    for sec, r in best.items():
        if sectors and sec not in sectors:
            continue
        cp = _cache_path(tic, sec, r["author"], r["exptime_s"])
        info = dict(sector=sec, author=r["author"], exptime_s=r["exptime_s"], cache=str(cp))
        if not cp.exists():
            try:
                lc = sr[r["index"]].download(quality_bitmask="default")
                col = "pdcsap_flux" if ("pdcsap_flux" in lc.colnames
                                        and np.isfinite(lc["pdcsap_flux"]).sum() > 100) else "flux"
                lc = lc.select_flux(col).remove_nans().normalize()
                t = np.asarray(lc.time.value, float)
                f = np.asarray(lc.flux.value, float)
                np.savez_compressed(cp, t=t, f=f, flux_column=col)
            except Exception as e:  # noqa: BLE001
                info["error"] = f"{type(e).__name__}: {e}"
                out.append(info)
                continue
        z = np.load(cp)
        t, f = z["t"], z["f"]
        n_bad = int(np.sum(f < 1 - max_depth))
        info.update(flux_column=str(z["flux_column"]), n_points=int(len(t)),
                    btjd_start=float(t.min()), btjd_end=float(t.max()),
                    n_points_below_artefact_depth=n_bad)
        if n_bad > 10:
            info["warning"] = (f"{n_bad} points dip deeper than {max_depth:.0%}: likely pipeline artefacts "
                               "(seen in QLP); exclude this sector or let find_eclipses reject them.")
        out.append(info)
    return out


def load_segments(tic: int, exclude_sectors: list[int] | None = None,
                  authors: list[str] | None = None) -> list[Segment]:
    """Load cached light curves (run ph_mast_download_lightcurves first)."""
    d = target_dir(tic) / "lc"
    chosen: dict[int, tuple] = {}
    for p in sorted(d.glob("S*_*.npz")) if d.exists() else []:
        sec_s, author, exp_s = p.stem.split("_", 2)
        sec, exp = int(sec_s[1:]), float(exp_s[:-1])
        if (exclude_sectors and sec in exclude_sectors) or (authors and author not in authors):
            continue
        if author not in AUTHOR_PREF:
            continue
        # Several products may be cached for one sector; use exactly one so no data is double counted.
        if sec not in chosen or _rank(author, exp) < _rank(chosen[sec][1], chosen[sec][2]):
            chosen[sec] = (p, author, exp)
    segs = []
    for sec, (p, author, exp) in sorted(chosen.items()):
        z = np.load(p)
        segs.append(Segment(sector=sec, author=author, t=z["t"], f=z["f"], exptime=exp))
    if not segs:
        raise FileNotFoundError(f"No cached light curves for TIC {tic}; call ph_mast_download_lightcurves first.")
    return segs


def concat(segs: list[Segment]):
    t = np.concatenate([s.t for s in segs])
    f = np.concatenate([s.f for s in segs])
    o = np.argsort(t)
    return t[o], f[o]
