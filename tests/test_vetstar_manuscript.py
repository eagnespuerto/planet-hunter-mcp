"""Report triage, manuscript checks and server wiring (no network)."""
import json

import pytest
from mcp import Client

from planet_hunter_mcp import manuscript, vetstar
from planet_hunter_mcp.server import mcp

REPORT_TEXT = """TESS Vetting Report
TIC 123456789
Verdict: Eclipsing binary candidate
Category: eclipsing_binary_candidate    Confidence: 85%
TIC ID
123456789
Sector / Camera / CCD
12 / 1 / 2
RA, Dec (deg)
220.1957, -73.0013
Radius (R_sun)
2.89
CROWDSAP
0.95
Discrete dip events
1
BLS period (d)
19.6478
BLS transits in window
2
Lomb-Scargle top period (d)
9.8239
Depth — odd transits
0.10100
Depth — even transits
0.00050
EB flag?
yes
Companion radius (R_Jup)
9.03
Transit Depth ***
1000000
ppm
#
t_start
t_end
Dur (h)
Depth (%)
1
1628.150
1628.440
7.00
10.30
"""


def test_parse_and_red_flags():
    r = vetstar.parse_report_text(REPORT_TEXT)
    assert r["tic"] == 123456789 and r["sector"] == 12
    assert r["ra"] == pytest.approx(220.1957)
    assert len(r["dips"]) == 1 and r["dips"][0]["depth"] == pytest.approx(0.103)
    codes = {f["code"] for f in vetstar.red_flags(r)["flags"]}
    assert {"bls_period_artifact", "odd_even_zero_depth", "placeholder_depth",
            "ls_harmonic", "too_large_for_planet"} <= codes
    assert vetstar.red_flags(r)["report_reliable"] is False


def test_alternating_depths_flag():
    r = dict(dips=[dict(depth=d) for d in (0.10, 0.02, 0.10, 0.02, 0.10)], odd_even_flag=False)
    assert "odd_even_blind" in {f["code"] for f in vetstar.red_flags(r)["flags"]}


def test_scaffold_and_check(tmp_path):
    res = manuscript.scaffold_rnaas(
        tmp_path, "note", title="A Test", author="Ada Lovelace", orcid=None, affiliation="Nowhere, Earth",
        email="a@example.org", star_label="HD 1", keywords=[("Eclipsing binary stars", 444)],
        pipelines=["SPOC", "TESS-SPOC"], numbers={"ephemeris": {"T0_bjd_tdb": 2459335.60643, "e_T0": 3.6e-4,
                                                               "P": 39.29559, "e_P": 9e-6}})
    tex = (tmp_path / "note.tex").read_text(encoding="utf-8")
    assert r"\dataset[doi:10.17909/t9-nmc8-f686]" in tex and "fwdt-2x66" in tex
    assert "2459335.60643(36)" in tex and "39.295590(9)" in tex
    assert "note_fig1.pdf" in tex
    chk = manuscript.check_rnaas(tmp_path / "note.tex")
    assert chk["checks"]["figure_name_differs_from_job"]
    assert chk["checks"]["ai_disclosure_present"] and chk["checks"]["originating_tool_credited"]
    assert not chk["checks"]["no_TODO_left"]          # scaffold leaves TODOs on purpose
    assert not chk["checks"]["abstract_words<=150"]   # empty abstract fails
    assert res["dataset_dois"][-1] == "10.17909/fwdt-2x66"


def test_word_limits(tmp_path):
    long_abs = " ".join(["word"] * 160)
    p = tmp_path / "x.tex"
    p.write_text(rf"""\documentclass{{aastex701}}\begin{{document}}
\begin{{abstract}}{long_abs}\end{{abstract}}
\section{{Intro}} {" ".join(["body"] * 1001)}
\begin{{acknowledgments}}AI disclosure: none. Vetstar.\end{{acknowledgments}}
\end{{document}}""", encoding="utf-8")
    chk = manuscript.check_rnaas(p)
    assert chk["abstract_words"] == 160 and chk["body_words"] >= 1001
    assert "abstract_words<=150" in chk["failed"] and "body_words<=1000" in chk["failed"]


async def test_server_lists_tools_and_never_publishes():
    async with Client(mcp) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert {"ph_vetstar_vet", "ph_fit_ephemeris", "ph_rnaas_check", "ph_zenodo_prepare_bundle"} <= names
        assert not any(w in n for n in names for w in ("upload", "submit", "publish", "push"))
        r = await c.call_tool("ph_quicklook_physics", {"depth": 0.103, "rstar_rsun": 2.89})
        assert json.loads(r.content[0].text)["too_large_for_planet"] is True
