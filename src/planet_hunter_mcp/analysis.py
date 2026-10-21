"""Pure-numpy eclipse analysis (spec stages S1, S2, S5–S7).

Generalised from the HD 128329 reference scripts (period_alias.py, refine.py, secondary.py,
paper_numbers.py). Nothing here touches the network, so every function is unit-testable with
synthetic light curves. Times are BTJD (days), fluxes normalised to ~1.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import curve_fit

_trapz = getattr(np, "trapezoid", None) or np.trapz

H = 6.62607015e-34
C = 2.99792458e8
KB = 1.380649e-23
RSUN_RJUP = 9.731
RSUN_REARTH = 109.08
RSUN_AU = 1 / 215.032


@dataclass
class Segment:
    sector: int
    author: str
    t: np.ndarray
    f: np.ndarray
    exptime: float = 0.0


# --------------------------------------------------------------------------- models

def trapezoid(t, t0, depth, T14, T23):
    """Symmetric trapezoid; T14/T23 in the same unit as t."""
    t = np.asarray(t, float)
    x = np.abs(t - t0)
    T14 = abs(T14)
    T23 = min(abs(T23), T14 * 0.99)
    y = np.ones_like(t)
    full = x <= T23 / 2
    ing = (x > T23 / 2) & (x < T14 / 2)
    y[full] -= depth
    y[ing] -= depth * (T14 / 2 - x[ing]) / ((T14 - T23) / 2)
    return y


def bin_lc(t, f, minutes: float, min_count: int = 1):
    """Fixed-width bins (mean time, median flux, count)."""
    bw = minutes / 1440
    bid = np.floor(np.asarray(t) / bw).astype(np.int64)
    ub, inv, cnt = np.unique(bid, return_inverse=True, return_counts=True)
    bt = np.bincount(inv, weights=np.asarray(t, float)) / cnt
    order = np.argsort(inv, kind="stable")
    splits = np.cumsum(cnt)[:-1]
    bf = np.array([np.median(g) for g in np.split(np.asarray(f)[order], splits)])
    keep = cnt >= min_count
    return bt[keep], bf[keep], cnt[keep]


# ------------------------------------------------------------------ S1 quick look

def band_flux(T: float, lo_nm: float = 600, hi_nm: float = 1000) -> float:
    """Planck B_lambda integrated over a crude TESS band (arbitrary units)."""
    lam = np.linspace(lo_nm * 1e-9, hi_nm * 1e-9, 400)
    return float(_trapz(1 / lam**5 / np.expm1(H * C / (lam * KB * T)), lam))


def companion_quicklook(depth: float, rstar_rsun: float, crowdsap: float = 1.0,
                        secondary_depth: float | None = None, teff1: float | None = None) -> dict:
    """R2 ≈ sqrt(depth/CROWDSAP)·R★; optional T2 from the secondary/primary depth ratio."""
    if not 0 < depth < 1:
        raise ValueError("depth must be a fraction in (0, 1), e.g. 0.103 for 10.3 %")
    crowd = crowdsap if crowdsap and crowdsap > 0 else 1.0
    d_corr = min(depth / crowd, 1.0)
    r2 = np.sqrt(d_corr) * rstar_rsun
    out = dict(depth_observed=depth, crowdsap=crowd, depth_dilution_corrected=d_corr,
               k=np.sqrt(d_corr), R2_rsun=r2, R2_rjup=r2 * RSUN_RJUP, R2_rearth=r2 * RSUN_REARTH,
               too_large_for_planet=bool(r2 * RSUN_RJUP > 2.0))
    out["note"] = ("R2 > 2 R_Jup: not a planet (stellar/brown-dwarf companion or blend)."
                   if out["too_large_for_planet"] else "Planet-sized; continue vetting.")
    if secondary_depth is not None and teff1:
        out["T2_from_depth_ratio_K"] = teff_from_surface_brightness(secondary_depth / depth, teff1)
    return out


def teff_from_surface_brightness(ratio: float, teff1: float) -> float | None:
    """Solve band_flux(T2)/band_flux(T1) = ratio for T2 by bisection (secondary/primary depth ratio)."""
    target = ratio * band_flux(teff1)
    lo, hi = 1000.0, 60000.0
    if not band_flux(lo) <= target <= band_flux(hi):
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if band_flux(mid) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def expected_secondary(depth: float, teff1: float, teff2_list=(3000, 3500, 4000, 5000, 5500)) -> dict:
    """Circular-orbit secondary depth ≈ δ_pri × B(T2)/B(T1) in the TESS band."""
    b1 = band_flux(teff1)
    return {int(T2): depth * band_flux(T2) / b1 for T2 in teff2_list}


# ------------------------------------------------------------------ S2 contamination

TESS_PIXEL_ARCSEC = 21.0


def contamination(depth: float, target_mag: float, neighbours: list[dict],
                  centroid_sigma: float | None = None, min_sep_pixels: float = 2.0) -> dict:
    """For each neighbour {name, mag, sep_arcsec}: flux ratio f and required intrinsic dip δ_nb.

    δ_nb = δ·(1+f)/f. δ_nb > 1 → impossible; > 0.5 → implausible without a centroid shift.
    The flux test alone cannot clear a bright star a few pixels away (it needs only a small dip), so,
    per spec S2, a neighbour ≥ `min_sep_pixels` TESS pixels away is excluded when the centroid offset
    is < 3σ: a dip on that star would have pulled the centroid toward it.
    """
    on_target_centroid = centroid_sigma is not None and abs(centroid_sigma) < 3
    rows = []
    for nb in neighbours:
        f = 10 ** (-0.4 * (nb["mag"] - target_mag))
        d_nb = depth * (1 + f) / f
        sep_px = nb.get("sep_arcsec", 0) / TESS_PIXEL_ARCSEC
        status = "impossible" if d_nb > 1 else ("implausible" if d_nb > 0.5 else "possible")
        if status != "impossible" and on_target_centroid and sep_px >= min_sep_pixels:
            status = "excluded_by_centroid"
        rows.append({**nb, "sep_pixels": sep_px, "flux_ratio": f, "required_neighbour_dip": d_nb,
                     "status": status})
    rows.sort(key=lambda r: r["required_neighbour_dip"])
    viable = [r for r in rows if r["status"] == "possible"]
    if viable:
        verdict = ("contaminant possible: " + ", ".join(f"{r['name']} ({r['sep_pixels']:.1f} px)" for r in viable[:3])
                   + (" — supply the centroid σ" if centroid_sigma is None
                      else " — within the centroid resolution; check difference images"))
    else:
        verdict = "on target (no neighbour can produce the dip" + (
            " given the on-target centroid)" if on_target_centroid else ")")
    return dict(depth=depth, target_mag=target_mag, neighbours=rows, verdict=verdict,
                n_possible=len(viable), centroid_sigma=centroid_sigma)


# ----------------------------------------------------------------- S5 eclipse finding

def find_eclipses(segments: list[Segment], dip_thresh: float = 0.03, max_depth: float = 0.5,
                  bin_minutes: float = 30, fit_window: float = 0.6) -> dict:
    """Locate dips in 30-min bins, fit trapezoids, flag full coverage and artefacts."""
    events, rejected = [], []
    for s in segments:
        if len(s.t) < 20:
            continue
        bt, bf, _ = bin_lc(s.t, s.f, bin_minutes)
        base = np.median(bf)
        low = np.where(bf < base * (1 - dip_thresh))[0]
        if not len(low):
            continue
        groups = np.split(low, np.where(np.diff(low) > 2)[0] + 1)
        for g in groups:
            if len(g) < 2:
                continue
            tguess = bt[g][np.argmin(bf[g])]
            dguess = 1 - bf[g].min() / base
            if dguess > max_depth:
                rejected.append(dict(sector=s.sector, author=s.author, t_guess=tguess, depth=dguess,
                                     reason=f"depth {dguess:.2f} > {max_depth}: pipeline artefact (e.g. QLP near-zero flux run)"))
                continue
            m = np.abs(s.t - tguess) < fit_window
            tt, ff = s.t[m], s.f[m]
            dur0 = max((bt[g[-1]] - bt[g[0]]) + bin_minutes / 1440, 2 / 24)
            try:
                p, cov = curve_fit(trapezoid, tt, ff, p0=[tguess, dguess, dur0, dur0 / 3], maxfev=20000)
                err = np.sqrt(np.abs(np.diag(cov)))
            except Exception as ex:  # noqa: BLE001
                rejected.append(dict(sector=s.sector, author=s.author, t_guess=tguess, depth=dguess,
                                     reason=f"trapezoid fit failed: {ex}"))
                continue
            t0, dep, T14, T23 = p[0], p[1], abs(p[2]), min(abs(p[3]), abs(p[2]))
            pre = np.sum((tt > t0 - T14 / 2) & (tt < t0))
            post = np.sum((tt < t0 + T14 / 2) & (tt > t0))
            full = bool(pre > 5 and post > 5 and tt.min() < t0 - T14 / 2 and tt.max() > t0 + T14 / 2)
            # A converged fit has finite, small errors; otherwise keep the event for inspection only.
            fit_ok = bool(np.isfinite(err).all() and err[1] < 0.5 * abs(dep) and err[0] < T14 / 2)
            events.append(dict(sector=s.sector, author=s.author, t0=t0, e_t0=err[0], depth=dep,
                               e_depth=err[1], T14_h=T14 * 24, T23_h=T23 * 24, fully_covered=full and fit_ok,
                               fit_ok=fit_ok))
    events.sort(key=lambda e: e["t0"])
    return dict(events=events, rejected=rejected,
                n_full=sum(e["fully_covered"] for e in events),
                note="Use only fully_covered events for the ephemeris; partial-event centroids are biased.")


# ---------------------------------------------------------------- S6 ephemeris/aliases

def _bootstrap_period(t: np.ndarray, P0: float, tol_days: float) -> float | None:
    """Phase-connect eclipse times in time order, refitting T0 and P after each one.

    Refitting is what keeps a long baseline from drifting: a 0.001 d error in P0 grows to ~1 d after
    1000 cycles, so checking all separations against an unrefined P0 picks spurious sub-harmonics.
    Growth starts at the closest pair and adds events nearest-first, so each hop is short."""
    i0 = int(np.argmin(np.diff(t)))
    order = np.argsort(np.abs(t - t[i0]), kind="stable")
    t = t[order]
    T0, P = t[0], P0
    for k in range(1, len(t)):
        E = np.round((t[: k + 1] - T0) / P)
        if np.abs(t[k] - (T0 + P * E[-1])) > tol_days:
            return None
        if len(np.unique(E)) >= 2:
            P, T0 = np.polyfit(E, t[: k + 1], 1)
    res = t - (T0 + P * np.round((t - T0) / P))
    return float(P) if np.all(np.abs(res) < tol_days) else None


def trial_period(t0s: list[float], tol_days: float = 0.05, max_n: int = 200) -> float:
    """Largest P (closest-pair separation / n) whose bootstrapped ephemeris phase-connects every eclipse."""
    t = np.unique(np.round(np.sort(np.asarray(t0s, float)), 5))
    if len(t) < 2:
        raise ValueError("Need at least two distinct eclipse times to estimate a period.")
    dmin = float(np.min(np.diff(t)))
    for n in range(1, max_n + 1):
        P = _bootstrap_period(t, dmin / n, tol_days)
        if P is not None:
            return P
    raise ValueError(f"No period ≤ {dmin:.4f} d phase-connects all eclipses within {tol_days} d; "
                     "pass trial_period explicitly or exclude events with poor timing.")


def fit_ephemeris(events: list[dict], trial_p: float | None = None, sigma_floor_d: float = 2e-4) -> dict:
    """Weighted linear ephemeris T(E) = T0 + P·E from fully covered events."""
    use = [e for e in events if e.get("fully_covered", True) and e.get("fit_ok", True)]
    if len(use) < 2:
        raise ValueError("Need ≥2 fully covered eclipses (spec gate wants ≥3 across >1 season).")
    T = np.array([e["t0"] for e in use])
    P0 = trial_p or trial_period(list(T))
    ref = T.min()
    E = np.round((T - ref) / P0).astype(int)
    S = np.array([max(e.get("e_t0", 0) or 0, sigma_floor_d) for e in use])
    A = np.vstack([np.ones_like(E, float), E]).T
    W = np.diag(1 / S**2)
    covp = np.linalg.inv(A.T @ W @ A)
    T0, P = covp @ A.T @ W @ T
    eT0, eP = np.sqrt(np.diag(covp))
    oc_min = (T - (T0 + P * E)) * 1440
    durs = [e.get("T14_h") for e in use if e.get("T14_h")]
    span_d = T.max() - T.min()
    gate = dict(n_full=len(use), span_days=span_d,
                ge3_events=len(use) >= 3, spans_gt_one_season=span_d > 200,
                oc_rms_min=float(np.sqrt(np.mean(oc_min**2))),
                oc_rms_vs_duration=(float(np.sqrt(np.mean(oc_min**2)) / (np.median(durs) * 60)) if durs else None))
    return dict(T0_btjd=T0, e_T0=eT0, T0_bjd_tdb=T0 + 2457000.0, P=P, e_P=eP, trial_P=P0,
                epochs=E.tolist(), times=T.tolist(), oc_min=oc_min.tolist(),
                sectors=[e.get("sector") for e in use], authors=[e.get("author") for e in use],
                depths=[e.get("depth") for e in use], gate=gate)


def alias_tests(t, f, T0: float, P: float, dip_thresh: float = 0.03, bin_minutes: float = 10,
                events: list[dict] | None = None) -> dict:
    """P/2, P/3: count predicted mid-times with data and no dip (any → excluded).
    2P: even vs odd epoch depths, compared within the same pipeline."""
    # Require 3 points per bin for 2-min data, but accept single points for 10/30-min FFI cadences.
    cad_min = float(np.median(np.diff(np.sort(t)))) * 1440 if len(t) > 1 else bin_minutes
    bt, bf, _ = bin_lc(t, f, bin_minutes, min_count=max(1, min(3, int(bin_minutes / max(cad_min, 1e-3)))))
    order = np.argsort(bt)
    bt, bf = bt[order], bf[order]
    bw = bin_minutes / 1440
    out = {}
    for lab, Pt in (("P/3", P / 3), ("P/2", P / 2), ("P", P)):
        k = np.arange(np.floor((bt[0] - T0) / Pt) - 1, np.ceil((bt[-1] - T0) / Pt) + 2)
        tp = T0 + k * Pt
        j = np.clip(np.searchsorted(bt, tp), 1, len(bt) - 1)
        j = np.where(np.abs(bt[j - 1] - tp) < np.abs(bt[j] - tp), j - 1, j)
        covered = np.abs(bt[j] - tp) < bw
        dip = (1 - bf[j]) > dip_thresh
        miss, hit = int((covered & ~dip).sum()), int((covered & dip).sum())
        out[lab] = dict(period=Pt, covered=int(covered.sum()), covered_with_dip=hit,
                        covered_without_dip=miss,
                        excluded=bool(miss > 0) if lab != "P" else None)
    if events:
        by_author: dict[str, dict[str, list]] = {}
        for e in events:
            if not e.get("fully_covered", True):
                continue
            ep = int(round((e["t0"] - T0) / P))
            g = by_author.setdefault(e.get("author", "?"), {"even": [], "odd": []})
            g["odd" if ep % 2 else "even"].append(e["depth"])
        twoP = {}
        for a, g in by_author.items():
            if g["even"] and g["odd"]:
                de, do = np.mean(g["even"]), np.mean(g["odd"])
                twoP[a] = dict(even_mean=de, odd_mean=do, n_even=len(g["even"]), n_odd=len(g["odd"]),
                               rel_diff=abs(de - do) / max(de, do))
            else:
                twoP[a] = dict(note="only one parity observed in this pipeline; 2P untestable here", **{k: len(v) for k, v in g.items()})
        out["2P"] = twoP
    out["all_shorter_aliases_excluded"] = bool(out["P/2"]["excluded"] and out["P/3"]["excluded"])
    # The adopted period must itself predict dips wherever data exist (allow ~10 % edge/noise misses);
    # otherwise it is a sub-harmonic and the alias exclusions above are meaningless.
    pc = out["P"]
    out["P_consistent"] = bool(pc["covered"] > 0 and pc["covered_without_dip"] <= max(1, 0.1 * pc["covered"]))
    if not out["P_consistent"]:
        out["warning"] = (f"Adopted P predicts {pc['covered_without_dip']} of {pc['covered']} covered epochs "
                          "with no dip: wrong period (likely a sub-harmonic). Re-run with trial_period.")
    return out


# ---------------------------------------------------------------- S7 secondary/orbit

def secondary_search(t, f, T0: float, P: float, step: float = 0.001, half_window_h: float = 1.5,
                     base_lo_h: float = 2.5, base_hi_h: float = 6.0,
                     phase_range=(0.03, 0.97), primary_T14_h: float | None = None) -> dict:
    """Per-visit local-baseline depth vs phase, weighted mean per trial phase, red-noise inflated.

    Never uses a global median baseline (sector offsets mimic multi-sigma secondaries).
    In-primary points are masked (given primary_T14_h) and trial phases whose in-window would touch
    the primary are skipped, so short periods don't leak the primary into baselines.
    """
    t = np.asarray(t, float)
    f = np.asarray(f, float)
    o = np.argsort(t)
    t, f = t[o], f[o]
    ph = ((t - T0) / P) % 1
    lo, hi = phase_range
    if primary_T14_h:
        half_pri = (0.6 * primary_T14_h) / 24 / P          # T14/2 plus a 20 % margin
        keep = np.minimum(ph, 1 - ph) > half_pri
        t, f, ph = t[keep], f[keep], ph[keep]
        edge = half_pri + half_window_h / 24 / P
        lo, hi = max(lo, edge), min(hi, 1 - edge)
    if hi <= lo:
        raise ValueError("The search windows are wider than the orbit allows; reduce half_window_h/base_hi_h.")
    grid = np.arange(lo, hi, step)
    w_in, b_lo, b_hi = half_window_h / 24 / P, base_lo_h / 24 / P, base_hi_h / 24 / P
    sd = np.full(len(grid), np.nan)
    se = np.full(len(grid), np.nan)
    nv = np.zeros(len(grid), int)
    cyc_all = (t - T0) / P
    for j, g in enumerate(grid):
        d = (ph - g + 0.5) % 1 - 0.5
        ad = np.abs(d)
        near = ad < b_hi
        if not near.any():
            continue
        tn_in = ad[near] < w_in
        tn_b = ad[near] > b_lo
        cyc = np.round(cyc_all[near] - g)
        fn = f[near]
        vals, wts = [], []
        for c in np.unique(cyc[tn_in]):
            mc = cyc == c
            mi, mb = tn_in & mc, tn_b & mc
            if mi.sum() < 10 or mb.sum() < 20:
                continue
            vals.append(np.median(fn[mb]) - np.median(fn[mi]))
            wts.append(mi.sum() / (1.25 * np.std(fn[mb])) ** 2)
        if vals:
            vals, wts = np.array(vals), np.array(wts)
            sd[j] = np.sum(vals * wts) / wts.sum()
            se[j] = 1 / np.sqrt(wts.sum())
            nv[j] = len(vals)
    if not np.isfinite(sd).any():
        raise ValueError("No phase had enough in-window and baseline points; check T0/P and data coverage.")
    snr = sd / se
    infl = float(max(1.4826 * np.nanmedian(np.abs(snr - np.nanmedian(snr))), 1.0))
    se_i = se * infl
    j5 = int(np.argmin(np.abs(grid - 0.5)))
    jm = int(np.nanargmax(sd / se_i))
    lim3 = 3 * float(np.nanmedian(se_i))
    # Lone-spike assessment: how wide is the >3σ excursion and how many visits feed it?
    above = (sd / se_i) > 3
    width = 0
    if above[jm]:
        a, b = jm, jm
        while a > 0 and above[a - 1]:
            a -= 1
        while b < len(above) - 1 and above[b + 1]:
            b += 1
        width = b - a + 1
    # A real secondary lasts about as long as the primary, so it stays above 3σ over ~T14 of phase;
    # an excursion narrower than half of that (or fed by a single visit) is a noise spike.
    dur_h = primary_T14_h or 2 * half_window_h
    expect_width = dur_h / 24 / P / step
    lone = bool(above[jm] and (nv[jm] <= 1 or width < max(2.0, 0.5 * expect_width)))
    return dict(phase_grid_step=step, inflation_factor=infl, median_1sigma=float(np.nanmedian(se_i)),
                limit_3sigma=lim3, depth_at_0p5=float(sd[j5]), err_at_0p5=float(se_i[j5]),
                visits_at_0p5=int(nv[j5]), max_phase=float(grid[jm]), max_depth=float(sd[jm]),
                max_err=float(se_i[jm]), max_snr=float(sd[jm] / se_i[jm]), max_visits=int(nv[jm]),
                max_excursion_width_points=width, expected_width_points=round(expect_width, 1), lone_spike=lone,
                n_points_above_5sigma=int(np.sum((sd / se_i) > 5)),
                phases_without_visits_frac=float(np.mean(nv == 0)),
                _curve=dict(grid=grid, depth=sd, err=se_i, visits=nv))


def orbit_geometry(depth: float, T14_h: float, P: float, R1_rsun: float, M1_msun: float,
                   M2_msun: float = 0.9) -> dict:
    """k_min, a/R1, circular central duration, implied b and the e·sinω needed to miss the secondary."""
    kmin = float(np.sqrt(depth))
    a_au = ((M1_msun + M2_msun) * (P / 365.25) ** 2) ** (1 / 3)
    aR = a_au / RSUN_AU / R1_rsun
    Tmax_h = P / np.pi * (1 + kmin) / aR * 24
    b = float(np.sqrt((1 + kmin) ** 2 - (T14_h / Tmax_h * (1 + kmin)) ** 2)) if T14_h < Tmax_h else 0.0
    need = (1 + kmin) / b if b > 0 else np.inf
    esinw = (need - 1) / (need + 1) if np.isfinite(need) else None
    grazing = bool(T14_h < 0.5 * Tmax_h)
    return dict(k_min=kmin, R2_min_rsun=kmin * R1_rsun, a_au=a_au, a_over_R1=aR,
                T14_central_circular_h=Tmax_h, b_circular=b, grazing_if_circular=grazing,
                esinw_min_to_hide_secondary=esinw,
                note=("If no secondary is seen and R2 is large, an eccentric orbit whose secondary "
                      "conjunction misses the star is implied (e·sinω ≳ value above); a hierarchical "
                      "triple/blend is the alternative."))


def _local_norm(t, f, tc, inner_h: float, outer_h: float):
    """Divide by a line fitted to inner–outer hours either side of mid-eclipse."""
    inner, outer = inner_h / 24, outer_h / 24
    m = np.abs(t - tc) < outer
    b = m & (np.abs(t - tc) > inner)
    if b.sum() < 20 or np.sum(b & (t < tc)) < 5 or np.sum(b & (t > tc)) < 5:
        return None
    c = np.polyfit(t[b] - tc, f[b], 1)
    return t[m], f[m] / np.polyval(c, t[m] - tc)


def _trap_hours(x, d, T14, T23, x0):
    return trapezoid(x, x0, d, T14, T23)


def stack_primary(segments: list[Segment], T0: float, P: float, inner_h: float | None = None,
                  outer_h: float | None = None, fit_group: str = "2min") -> dict:
    """Locally normalise every covered primary, stack in hours from mid-eclipse, fit a trapezoid.

    Groups: '2min' (exptime ≤ 200 s) and 'ffi'. The fit uses `fit_group` if it has data, else all."""
    groups: dict[str, list[list]] = {"2min": [[], []], "ffi": [[], []]}
    used = []
    for s in segments:
        k = np.arange(np.floor((s.t.min() - T0) / P), np.ceil((s.t.max() - T0) / P) + 1)
        for tc in T0 + k * P:
            r = _local_norm(s.t, s.f, tc, inner_h or 4.5, outer_h or 10)
            if r is None:
                continue
            tt, ff = r
            if np.sum(np.abs(tt - tc) < 1 / 24) < 3:
                continue
            g = "2min" if 0 < s.exptime <= 200 else "ffi"
            groups[g][0].append((tt - tc) * 24)
            groups[g][1].append(ff)
            used.append(dict(sector=s.sector, author=s.author, t_mid=float(tc), epoch=int(round((tc - T0) / P))))
    arr = {g: (np.concatenate(v[0]), np.concatenate(v[1])) if v[0] else (np.array([]), np.array([]))
           for g, v in groups.items()}
    fx, ff = arr[fit_group] if len(arr[fit_group][0]) > 50 else (
        np.concatenate([a[0] for a in arr.values()]), np.concatenate([a[1] for a in arr.values()]))
    if len(fx) < 50:
        raise ValueError("Too few in-eclipse points to stack; check the ephemeris and cached sectors.")
    # Starting guess from the stack itself (0.1-h binned medians): depth from the minimum, T14 from
    # the half-depth width scaled up, so planets (~1 %, ~2 h) and EBs (~10 %, ~7 h) both converge.
    edges = np.arange(fx.min(), fx.max() + 0.1, 0.1)
    idx = np.digitize(fx, edges)
    bx = np.array([fx[idx == k].mean() for k in np.unique(idx)])
    bf = np.array([np.median(ff[idx == k]) for k in np.unique(idx)])
    d0 = max(1 - bf.min(), 1e-4)
    half = np.abs(bx[bf < 1 - d0 / 2])
    T14_0 = 2.6 * half.max() if len(half) else 4.0
    p, cov = curve_fit(_trap_hours, fx, ff, p0=[d0, T14_0, T14_0 / 3, 0], maxfev=40000)
    err = np.sqrt(np.abs(np.diag(cov)))
    return dict(depth=p[0], e_depth=err[0], T14_h=abs(p[1]), e_T14_h=err[1], T23_h=max(p[2], 0),
                e_T23_h=err[2], x0_h=p[3], eclipses_used=used, fit_group=fit_group,
                _stack={g: dict(x=a[0], f=a[1]) for g, a in arr.items()}, _params=p.tolist())
