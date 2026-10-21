"""End-to-end regression: rerun the HD 128329 analysis through the MCP tools and compare with the
published values in AGENT_SPEC §4. Needs network on first run (MAST downloads are cached)."""
import asyncio
import json
import sys
import time

from mcp import Client

from planet_hunter_mcp.server import mcp

TIC = "402175877"
EXPECT = dict(T0_bjd=2459335.60643, e_T0=0.00036, P=39.295590, e_P=0.000009, depth=0.103, T14_h=7.1,
              sec05_ppm=-24, sec05_err_ppm=79, lim3_ppm=240, infl=1.48)


async def call(c, name, **args):
    t0 = time.time()
    r = await c.call_tool(name, args)
    txt = r.content[0].text
    print(f"\n### {name} ({time.time() - t0:.0f}s)\n{txt[:1500]}", flush=True)
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        sys.exit(f"{name} failed: {txt}")


async def main():
    async with Client(mcp, read_timeout_seconds=7200) as c:
        await call(c, "ph_resolve_target", identifier=f"TIC {TIC}")
        await call(c, "ph_mast_download_lightcurves", tic=TIC, authors=["SPOC", "TESS-SPOC", "QLP"])
        await call(c, "ph_find_eclipses", tic=TIC)
        eph = await call(c, "ph_fit_ephemeris", tic=TIC, exclude_sectors=[93])
        prim = await call(c, "ph_primary_stack", tic=TIC, exclude_sectors=[93], m1_msun=2.4)
        sec = await call(c, "ph_secondary_search", tic=TIC, exclude_sectors=[93])
        await call(c, "ph_contamination", tic=TIC, depth=prim["primary"]["depth"])
        await call(c, "ph_make_figure", tic=TIC)
    got = dict(T0_bjd=eph["T0_bjd_tdb"], e_T0=eph["e_T0"], P=eph["P"], e_P=eph["e_P"],
               depth=prim["primary"]["depth"], T14_h=prim["primary"]["T14_h"],
               sec05_ppm=sec["depth_at_0p5"] * 1e6, sec05_err_ppm=sec["err_at_0p5"] * 1e6,
               lim3_ppm=sec["limit_3sigma"] * 1e6, infl=sec["inflation_factor"])
    print("\n### comparison (expected vs got)")
    for k in EXPECT:
        print(f"{k:14s} {EXPECT[k]:>16.6f} {got[k]:>16.6f}")


asyncio.run(main())
