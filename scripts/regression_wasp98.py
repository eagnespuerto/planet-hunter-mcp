"""Known-planet control: WASP-98 b (TIC 166833457, TOI-404.01). Checks that the pipeline recovers the
catalogued period, does not pick a sub-harmonic over the ~8 yr baseline, raises no Vetstar red flags,
and clears neighbours using the centroid. Needs network on first run (MAST downloads are cached)."""
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
    E = round((TOI_EPOCH - eph["T0_bjd_tdb"]) / eph["P"])
    print(f"Vetstar S107 verdict: {vet['summary']['verdict']}; red flags: {vet['red_flags']['n_flags']}")
    print(f"P = {eph['P']:.7f} ± {eph['e_P']:.7f} d (archive {ARCHIVE_P}); O−C rms {eph['gate']['oc_rms_min']:.2f} min; "
          f"P consistent with data: {eph['aliases']['P_consistent']}")
    print(f"TOI epoch residual at E={E}: {(TOI_EPOCH - eph['T0_bjd_tdb'] - E * eph['P']) * 1440:+.2f} min")
    print(f"Depth {prim['primary']['depth'] * 100:.2f} %, T14 {prim['primary']['T14_h']:.2f} h; "
          f"secondary 3σ limit {sec['limit_3sigma'] * 1e6:.0f} ppm; contamination: {con['verdict']}")


asyncio.run(main())
