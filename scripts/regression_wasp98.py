"""Known-planet control: WASP-98 b (TIC 166833457, TOI-404.01). Checks that the pipeline recovers the
catalogued period, does not pick a sub-harmonic over the ~8 yr baseline, raises no Vetstar red flags,
and clears neighbours using the centroid. Then fits the limb-darkened (batman) transit and compares Rp/R★
and b with the NASA Exoplanet Archive, fetched live through ph_exoplanet_archive_query.
Needs network on first run (MAST downloads are cached)."""
import asyncio
import json

from mcp import Client

from planet_hunter_mcp.server import mcp

TIC = "166833457"
ARCHIVE_P, TOI_EPOCH = 2.96264036, 2459168.63923  # NASA Exoplanet Archive pscomppars / TOI table


async def call(c, name, **args):
    txt = (await c.call_tool(name, args)).content[0].text
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        raise SystemExit(f"{name} failed: {txt}")


async def main():
    async with Client(mcp, read_timeout_seconds=7200) as c:
        await call(c, "ph_resolve_target", identifier=f"TIC {TIC}")
        vet = await call(c, "ph_vetstar_vet", tic=TIC, sector=107)
        await call(c, "ph_mast_download_lightcurves", tic=TIC, authors=["SPOC", "TESS-SPOC"])
        await call(c, "ph_find_eclipses", tic=TIC, dip_threshold=0.01)
        eph = await call(c, "ph_fit_ephemeris", tic=TIC, dip_threshold=0.01)
        prim = await call(c, "ph_primary_stack", tic=TIC)
        sec = await call(c, "ph_secondary_search", tic=TIC)
        con = await call(c, "ph_contamination", tic=TIC, depth=prim["primary"]["depth"])
        ld = (await call(c, "ph_primary_stack", tic=TIC, model="batman"))["primary"]
        await call(c, "ph_secondary_search", tic=TIC)  # secondary masking now uses the batman T14
        fig = await call(c, "ph_make_figure", tic=TIC)
        cols = "pl_name,pl_ratror,pl_ratrorerr1,pl_ratrorerr2,pl_imppar,pl_impparerr1,pl_impparerr2,pl_ratdor,pl_trandur"
        comp = await call(c, "ph_exoplanet_archive_query",
                          adql=f"select {cols} from pscomppars where pl_name='WASP-98 b'")
        refs = await call(c, "ph_exoplanet_archive_query",
                          adql=f"select {cols},pl_refname from ps where pl_name='WASP-98 b'")
    E = round((TOI_EPOCH - eph["T0_bjd_tdb"]) / eph["P"])
    print(f"Vetstar S107 verdict: {vet['summary']['verdict']}; red flags: {vet['red_flags']['n_flags']}")
    print(f"P = {eph['P']:.7f} ± {eph['e_P']:.7f} d (archive {ARCHIVE_P}); O−C rms {eph['gate']['oc_rms_min']:.2f} min; "
          f"P consistent with data: {eph['aliases']['P_consistent']}")
    print(f"TOI epoch residual at E={E}: {(TOI_EPOCH - eph['T0_bjd_tdb'] - E * eph['P']) * 1440:+.2f} min")
    print(f"Depth {prim['primary']['depth'] * 100:.2f} %, T14 {prim['primary']['T14_h']:.2f} h; "
          f"secondary 3σ limit {sec['limit_3sigma'] * 1e6:.0f} ppm; contamination: {con['verdict']}")
    print(f"\nLimb-darkened fit (u1, u2 = {ld['u1']:.3f}, {ld['u2']:.3f}; prior {ld['ld_prior']}):")
    print(f"  Rp/R* {ld['rp_over_rstar']:.4f} ± {ld['e_rp_over_rstar']:.4f}, b {ld['b']:.3f} ± {ld['e_b']:.3f}, "
          f"a/R* {ld['a_over_rstar']:.2f} ± {ld['e_a_over_rstar']:.2f}")
    print(f"  depth {ld['depth'] * 100:.3f} ± {ld['e_depth'] * 100:.3f} %, T14 {ld['T14_h']:.3f} ± {ld['e_T14_h']:.3f} h, "
          f"T23 {ld['T23_h']:.3f} ± {ld['e_T23_h']:.3f} h; reduced chi2 {ld['chi2_reduced']:.2f}; figure {fig['files'][1]}")
    print("\nNASA Exoplanet Archive (live query):")
    for r in comp["rows"] + refs["rows"]:
        print("  " + ", ".join(f"{k}={v}" for k, v in r.items() if v is not None))
    ar = comp["rows"][0]
    for lab, mine, e_mine, key in (("Rp/R*", ld["rp_over_rstar"], ld["e_rp_over_rstar"], "pl_ratror"),
                                   ("b", ld["b"], ld["e_b"], "pl_imppar")):
        if ar.get(key) is None:
            print(f"{lab}: no pscomppars value")
            continue
        e_ar = max(abs(ar.get(key + "err1") or 0), abs(ar.get(key + "err2") or 0))
        z = (mine - ar[key]) / max((e_mine ** 2 + e_ar ** 2) ** 0.5, 1e-9)
        print(f"{lab}: this fit {mine:.4f} ± {e_mine:.4f} vs archive {ar[key]} ± {e_ar:g} → {z:+.1f} σ")


asyncio.run(main())
