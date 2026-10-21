"""Limb-darkened (batman) primary fit on synthetic transits (no network)."""
import numpy as np
import pytest

from planet_hunter_mcp import analysis as A

P, T0 = 2.9626, 1500.30
K, B, AR, U = 0.16, 0.55, 8.5, (0.42, 0.22)


def make_transit_segments(rng, exptime=120, noise=1e-3, k=K, b=B):
    cad = exptime / 86400
    segs = []
    for i, (a, e) in enumerate(((1490, 1517), (1720, 1747))):
        t = np.arange(a, e, cad)
        x_h = (((t - T0) / P + 0.5) % 1 - 0.5) * P * 24
        f = A._batman_model(x_h, P * 24, exptime)([0.0, k, b, AR, *U])
        f = f * (1 + 0.003 * i) + rng.normal(0, noise, len(t))
        segs.append(A.Segment(sector=i + 1, author="SPOC", t=t, f=f, exptime=exptime))
    return segs


@pytest.mark.parametrize("b_true", [0.2, 0.55, 0.8])
def test_batman_stack_recovers_k_and_b(b_true):
    segs = make_transit_segments(np.random.default_rng(11), b=b_true)
    st = A.stack_primary(segs, T0, P, model="batman", ld=dict(u1=U[0], u2=U[1], sigma=0.1, source="test"))
    assert st["model"] == "batman"
    assert st["rp_over_rstar"] == pytest.approx(K, abs=max(3 * st["e_rp_over_rstar"], 0.004))
    assert st["b"] == pytest.approx(b_true, abs=max(3 * st["e_b"], 0.05))
    t14, t23 = A.transit_durations_h(K, b_true, AR, P * 24)
    assert st["T14_h"] == pytest.approx(t14, abs=0.03)
    assert st["T23_h"] == pytest.approx(t23, abs=0.05)
    assert abs(st["x0_h"]) < 0.01
    for key in ("e_depth", "e_T14_h", "e_T23_h", "e_b", "e_rp_over_rstar"):
        assert np.isfinite(st[key]) and st[key] > 0
    # the trapezoid underfits the curved bottom of a limb-darkened transit
    assert st["trapezoid"]["depth"] < st["depth"]
    assert len(st["_params"]) == 8
    model = A.transit_model_hours(np.array([0.0, 5.0]), st["_params"])
    assert model[1] == pytest.approx(1.0) and 1 - model[0] == pytest.approx(st["depth"], rel=1e-6)


def test_batman_ffi_cadence_supersampled():
    segs = make_transit_segments(np.random.default_rng(5), exptime=600, noise=6e-4)
    st = A.stack_primary(segs, T0, P, model="batman", ld=dict(u1=U[0], u2=U[1], sigma=0.1))
    assert st["exptime_s"] == 600
    assert st["rp_over_rstar"] == pytest.approx(K, abs=0.006)
    assert st["b"] == pytest.approx(B, abs=0.08)


def test_trapezoid_default_unchanged():
    segs = make_transit_segments(np.random.default_rng(11))
    st = A.stack_primary(segs, T0, P)
    assert "model" not in st and "rp_over_rstar" not in st
    assert len(st["_params"]) == 4
    with pytest.raises(ValueError):
        A.stack_primary(segs, T0, P, model="spline")


def test_fixed_limb_darkening():
    segs = make_transit_segments(np.random.default_rng(2))
    st = A.stack_primary(segs, T0, P, model="batman", ld=dict(u1=U[0], u2=U[1], sigma=0))
    assert st["u1"] == U[0] and st["e_u1"] is None
    assert st["rp_over_rstar"] == pytest.approx(K, abs=0.004)


def test_claret_interp():
    rows = [dict(Teff=T, logg=g, a=0.5 - T / 1e5 + g / 100, b=0.2 + T / 1e5) for T in (5250, 5500) for g in (4.5, 5.0)]
    r = A.claret_interp(rows, 5375, 4.75)
    assert r["method"] == "bilinear"
    assert r["u1"] == pytest.approx(0.5 - 5375 / 1e5 + 4.75 / 100)
    assert r["u2"] == pytest.approx(0.2 + 5375 / 1e5)
    near = A.claret_interp(rows, 6000, 4.6)
    assert near["method"] == "nearest" and near["Teff_grid"] == 5500 and near["logg_grid"] == 4.5


def test_figure_draws_batman_model(tmp_path):
    from planet_hunter_mcp.figures import rnaas_figure

    segs = make_transit_segments(np.random.default_rng(11))
    st = A.stack_primary(segs, T0, P, model="batman", ld=dict(u1=U[0], u2=U[1], sigma=0.1))
    files = rnaas_figure(tmp_path / "fig", st["_stack"], st["_params"], P, None, model="batman")
    assert all((tmp_path / f).exists() for f in files)
