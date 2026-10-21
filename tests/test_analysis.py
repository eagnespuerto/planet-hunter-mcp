"""Synthetic-light-curve tests for the analysis core (no network)."""
import numpy as np
import pytest

from planet_hunter_mcp import analysis as A

P_TRUE, T0_TRUE, DEPTH, T14, T23 = 12.345, 1500.20, 0.08, 6.0 / 24, 1.5 / 24


def make_segments(rng, secondary_depth=0.0, sectors=((1490, 1515), (1720, 1745), (2100, 2125)), cadence_min=10):
    segs = []
    for i, (a, b) in enumerate(sectors):
        t = np.arange(a, b, cadence_min / 1440)
        t = t[~((t > (a + b) / 2 - 0.5) & (t < (a + b) / 2 + 0.5))]  # mid-sector downlink gap
        ph = ((t - T0_TRUE) / P_TRUE + 0.5) % 1 - 0.5
        f = A.trapezoid(ph * P_TRUE, 0.0, DEPTH, T14, T23)
        if secondary_depth:
            ph2 = ((t - T0_TRUE) / P_TRUE) % 1 - 0.5
            f = f - (1 - A.trapezoid(ph2 * P_TRUE, 0.0, secondary_depth, T14, T23))
        f = f * (1 + 0.002 * i) + rng.normal(0, 4e-4, len(t))  # per-sector offset + white noise
        segs.append(A.Segment(sector=i + 1, author="SPOC", t=t, f=f, exptime=120))
    return segs


@pytest.fixture
def rng():
    return np.random.default_rng(42)


def test_trapezoid_shape():
    t = np.linspace(-1, 1, 2001)
    y = A.trapezoid(t, 0, 0.1, 0.5, 0.1)
    assert y.min() == pytest.approx(0.9)
    assert y[0] == 1 and y[-1] == 1


def test_find_eclipses_and_ephemeris(rng):
    segs = make_segments(rng)
    res = A.find_eclipses(segs)
    assert res["n_full"] >= 4
    for e in res["events"]:
        assert e["depth"] == pytest.approx(DEPTH, rel=0.1)
    eph = A.fit_ephemeris(res["events"])
    assert eph["P"] == pytest.approx(P_TRUE, abs=1e-3)
    assert ((eph["T0_btjd"] - T0_TRUE) / P_TRUE) % 1 == pytest.approx(0, abs=1e-3) or \
        ((eph["T0_btjd"] - T0_TRUE) / P_TRUE) % 1 == pytest.approx(1, abs=1e-3)
    assert eph["gate"]["spans_gt_one_season"]
    t, f = np.concatenate([s.t for s in segs]), np.concatenate([s.f for s in segs])
    al = A.alias_tests(t, f, eph["T0_btjd"], eph["P"], events=res["events"])
    assert al["P/2"]["excluded"] and al["P/3"]["excluded"]
    assert al["P"]["covered_with_dip"] >= 4


def test_artefact_rejected(rng):
    segs = make_segments(rng)
    s = segs[0]
    bad = (s.t > 1496) & (s.t < 1496.3)
    s.f[bad] = 0.02  # QLP-style near-zero run
    res = A.find_eclipses(segs)
    assert any("artefact" in r["reason"] for r in res["rejected"])


def test_secondary_search_limit_and_detection(rng):
    segs = make_segments(rng)
    t, f = np.concatenate([s.t for s in segs]), np.concatenate([s.f for s in segs])
    o = np.argsort(t)
    res = A.secondary_search(t[o], f[o], T0_TRUE, P_TRUE, step=0.005)
    assert abs(res["depth_at_0p5"]) < 3 * res["err_at_0p5"]
    assert res["limit_3sigma"] < 5e-4
    # the per-sector offsets must not masquerade as a secondary
    assert res["n_points_above_5sigma"] == 0

    segs2 = make_segments(np.random.default_rng(1), secondary_depth=0.004)
    t2, f2 = np.concatenate([s.t for s in segs2]), np.concatenate([s.f for s in segs2])
    res2 = A.secondary_search(t2, f2, T0_TRUE, P_TRUE, step=0.005)
    assert res2["depth_at_0p5"] == pytest.approx(0.004, rel=0.25)


def test_quicklook_and_geometry_match_hd128329():
    q = A.companion_quicklook(0.103, 2.89)
    assert q["R2_rsun"] == pytest.approx(0.93, abs=0.01)
    assert q["too_large_for_planet"]
    g = A.orbit_geometry(0.103, 7.1, 39.29559, 2.89, 2.4, 0.9)
    assert g["a_over_R1"] == pytest.approx(25, abs=1.5)
    assert g["T14_central_circular_h"] == pytest.approx(16, abs=1.5)
    assert g["esinw_min_to_hide_secondary"] == pytest.approx(0.06, abs=0.03)


def test_planck_secondary_and_inversion():
    exp = A.expected_secondary(0.103, 9483, [5000])
    assert exp[5000] == pytest.approx(0.015, rel=0.2)
    ratio = A.band_flux(4000) / A.band_flux(9483)
    assert A.teff_from_surface_brightness(ratio, 9483) == pytest.approx(4000, rel=1e-3)


def test_contamination_formula():
    # HD 128329: G=11.7 neighbour vs G≈9.68 target, 10.3% eclipse → neighbour would need ~0.77 dip
    r = A.contamination(0.103, 9.68, [dict(name="nb", mag=11.7, sep_arcsec=118)])
    nb = r["neighbours"][0]
    assert nb["flux_ratio"] == pytest.approx(0.155, abs=0.01)
    assert nb["required_neighbour_dip"] == pytest.approx(0.77, abs=0.03)
    assert nb["status"] == "implausible"
