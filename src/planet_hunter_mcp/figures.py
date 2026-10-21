"""One-panel-pair RNAAS figure (spec S8): (a) stacked primary + model, (b) secondary search."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .analysis import _trap_hours


def rnaas_figure(out_stem: Path, stack: dict, params: list[float], P: float, sec_curve: dict | None,
                 expected_secondary: dict | None = None, ref_teff: int | None = None) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 9, "axes.linewidth": 0.8})
    ncol = 2 if sec_curve else 1
    fig, ax = plt.subplots(1, ncol, figsize=(7.2 if ncol == 2 else 3.6, 2.8),
                           gridspec_kw={"width_ratios": [1, 1.25]} if ncol == 2 else None)
    ax = np.atleast_1d(ax)
    ffi, two = stack.get("ffi", {}), stack.get("2min", {})
    if len(ffi.get("x", [])):
        ax[0].plot(ffi["x"], ffi["f"], ".", ms=2.5, color="#E69F00", alpha=0.6, label="FFI")
    if len(two.get("x", [])):
        ax[0].plot(two["x"], two["f"], ".", ms=1, color="k", alpha=0.25, label="2-min")
    T14 = params[1]
    xx = np.linspace(-1.5 * T14, 1.5 * T14, 800)
    ax[0].plot(xx, _trap_hours(xx, *params), color="#0072B2", lw=1.2, label="trapezoid")
    ax[0].set_xlim(-1.15 * T14, 1.15 * T14)
    ax[0].set_xlabel("Hours from primary eclipse")
    ax[0].set_ylabel("Normalized flux")
    ax[0].legend(fontsize=6.5, loc="lower left", frameon=False)
    ax[0].text(0.97, 0.05, f"P = {P:.5f} d", transform=ax[0].transAxes, ha="right", fontsize=8)
    ax[0].set_title("(a)", loc="left", fontsize=9)
    if sec_curve:
        g, d, e = (np.asarray(sec_curve[k], float) for k in ("grid", "depth", "err"))
        ax[1].fill_between(g, -3 * e * 1e6, 3 * e * 1e6, color="0.85", lw=0, label=r"$\pm3\sigma$")
        ax[1].plot(g, d * 1e6, color="k", lw=0.6)
        ax[1].axhline(0, color="0.5", lw=0.6)
        top = np.nanmax(np.abs(3 * e * 1e6)) * 2.5
        if expected_secondary:
            T = ref_teff or min(expected_secondary)
            val = expected_secondary[T] * 1e6
            if val < 3 * top:  # within reach: extend the axis and draw the reference line
                top = max(top, 1.3 * val)
                ax[1].axhline(val, color="#D55E00", ls="--", lw=1)
                ax[1].text(0.5, val + 0.03 * top, f"circular orbit, {T} K companion", ha="center",
                           va="bottom", fontsize=6.5, color="#D55E00")
            else:  # off-scale values go in a label, not a squashed axis
                ax[1].text(0.5, 0.95 * top, f"{T} K companion: {val:,.0f} ppm (off scale)", ha="center",
                           va="top", fontsize=6.5, color="#D55E00")
        lo = -np.nanmax(np.abs(3 * e * 1e6)) * 1.5
        ax[1].set_ylim(lo, top)
        ax[1].set_xlim(0, 1)
        ax[1].set_xlabel("Orbital phase")
        ax[1].set_ylabel("Depth at trial phase (ppm)")
        ax[1].set_title("(b)", loc="left", fontsize=9)
        ax[1].legend(fontsize=6.5, loc="lower right", frameon=False)
    fig.tight_layout()
    out = [str(out_stem.with_suffix(".pdf")), str(out_stem.with_suffix(".png"))]
    fig.savefig(out[0])
    fig.savefig(out[1], dpi=200)
    plt.close(fig)
    return out
