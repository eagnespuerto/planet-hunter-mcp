"""NEMESIS client helpers, ExoFOP bulk-upload ZIPs and the new tools' wiring (no network)."""
import base64
import json
import zipfile
from datetime import datetime, timezone

import pytest
from mcp import Client

from planet_hunter_mcp import exofop, nemesis
from planet_hunter_mcp.server import _nemesis_zip_files, mcp

NOW = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()


# ------------------------------------------------------------------ data tags

@pytest.mark.parametrize("tag", ["12345", "20261119_puerto_Vetstar-NEMESIS_1"])
def test_valid_tags(tag):
    assert exofop.check_data_tag(tag) is None


@pytest.mark.parametrize("tag, part", [
    ("", "required"), ("20261119_puerto_Vetstar_NEMESIS_1", "4 parts"), ("20261340_puerto_x_1", "real date"),
    ("20261119_puerto_bad desc_1", "part 3"), ("20261119_puerto_x_one", "part 4"),
])
def test_bad_tags(tag, part):
    assert part in exofop.check_data_tag(tag)


def test_make_data_tag_cleans_description():
    assert exofop.make_data_tag("puerto", "Vetstar NEMESIS (TLS) é", 1, NOW) == "20261119_puerto_Vetstar-NEMESIS-TLS-e_1"


def test_descriptor_text_is_ascii_without_pipes():
    assert exofop.descriptor_text("depth | 2 R⊕ — fold\n") == "depth 2 R_Earth - fold"


# ------------------------------------------------------------------ archive

def test_build_archive_names_descriptor_and_counters(tmp_path):
    a = tmp_path / "fold.png"
    a.write_bytes(b"png")
    b = tmp_path / "cut.fits"
    b.write_bytes(b"fits")
    files = [dict(path=str(a), label="Fold", code="L", key="nemesis_fold"),
             dict(path=str(a), label="Fold again", code="L", key="nemesis_fold"),
             dict(path=str(b), label="Raw FFI", code="I")]
    tag = "20261119_puerto_Vetstar-NEMESIS_1"
    out = exofop.build_archive(tic=149179763, initials="EAP", tag=tag, files=files, out_dir=tmp_path / "x",
                               sector=1, author="Ella Agnes Puerto", now=NOW)
    assert out["counter"] == "001"
    assert out["entries"] == ["eap20261119-001.txt", "TIC149179763L-eap20261119.nemesis-fold.png",
                              "TIC149179763L-eap20261119.nemesis-folda.png", "TIC149179763I-eap20261119.cut.fits"]
    with zipfile.ZipFile(out["zip"]) as z:
        assert z.namelist() == out["entries"]
        lines = z.read("eap20261119-001.txt").decode().splitlines()
    cols = lines[0].split("|")
    assert len(cols) == 5 and cols[1] == tag and cols[2] == "" and cols[3] == "0"
    assert cols[4] == "Vetstar - Fold (Sector 1), by Ella Agnes Puerto (eap)"
    # A second ZIP on the same UT day must not reuse the counter.
    again = exofop.build_archive(tic=149179763, initials="eap", tag=tag, files=files[:1], out_dir=tmp_path / "x",
                                 now=NOW)
    assert again["counter"] == "002" and again["zip"].endswith("eap20261119-002.zip")


def test_build_archive_rejects_bad_input(tmp_path):
    f = tmp_path / "a.png"
    f.write_bytes(b"x")
    with pytest.raises(ValueError, match="part 2|4 parts"):
        exofop.build_archive(tic=1, initials="eap", tag="20261119__x_1", files=[dict(path=str(f), code="O")],
                             out_dir=tmp_path)
    with pytest.raises(ValueError, match="file-type code"):
        exofop.build_archive(tic=1, initials="eap", tag="1", files=[dict(path=str(f), code="Z")], out_dir=tmp_path)
    with pytest.raises(ValueError, match="Initials"):
        exofop.build_archive(tic=1, initials="e1", tag="1", files=[dict(path=str(f), code="O")], out_dir=tmp_path)


@pytest.mark.parametrize("name, code", [
    ("tess-s0001-1-1_149179763_astrocut.fits", "I"), ("TIC1_tp.fits", "I"),
    ("nemesis_light_curve.png", "L"), ("nemesis_fold.png", "L"),
    ("vetstar_nemesis_TIC1_S001.pdf", "O"), ("nemesis_periodogram.png", "O"),
])
def test_file_type_code(name, code):
    assert exofop.file_type_code(name) == code


# ------------------------------------------------------------------ NEMESIS helpers

RESULT = {
    "primary": "TLS",
    "search": {"TLS": {"params": {"period": 3.4476, "depth_ppt": 0.77, "sde": 15.3, "periods": list(range(500))},
                       "leo": {"available": True, "disposition": "PC",
                               "tests": [{"key": "odd_even", "passed": True}, {"key": "v_shape", "passed": False}]}}},
    "figures": {"fold": PNG, "light_curve": "data:image/png;base64," + PNG, "odd_even": None},
    "star": {"ra": 1.0, "dec": 2.0}, "meta": {"tic": 149179763, "sector": 1},
}


def test_summary_and_strip():
    s = nemesis.summary(RESULT)
    assert s["primary_method"] == "TLS" and s["leo_disposition"] == "PC" and s["leo_failed_tests"] == ["v_shape"]
    assert s["params"]["periods"] == "<500 values omitted>" and s["params"]["period"] == 3.4476
    assert "figures" not in nemesis.strip_arrays(RESULT)


def test_save_figures_and_zip_split(tmp_path):
    saved = nemesis.save_figures(RESULT, tmp_path)
    assert set(saved) == {"fold", "light_curve"}
    assert (tmp_path / "nemesis_light_curve.png").read_bytes().startswith(b"\x89PNG")
    (tmp_path / "vetstar_nemesis_TIC149179763_S001.pdf").write_bytes(b"%PDF")
    (tmp_path / "files").mkdir()
    for n in ("TIC_149179763_Sector_0001_TLS_Report_multi_page.pdf", "TIC_149179763_Sector_0001_BLS_Report_multi_page.pdf", "page1.png", "tess-s0001_astrocut.fits"):
        (tmp_path / "files" / n).write_bytes(b"x")
    main, raw = _nemesis_zip_files(tmp_path, "TLS")
    assert [f["key"] for f in main] == ["nemesis-lc", "nemesis-tls-fold", "nemesis-vetting-report",
                                        "nemesis-tls-report"]
    assert [f["code"] for f in raw] == ["O", "O", "I"]


def test_bad_job_id():
    with pytest.raises(ValueError):
        nemesis.job_dir(1, "../etc")


async def test_new_tools_registered():
    async with Client(mcp) as c:
        names = {t.name for t in (await c.list_tools()).tools}
    assert {"ph_nemesis_status", "ph_nemesis_sectors", "ph_nemesis_run", "ph_nemesis_job",
            "ph_ogle_search", "ph_exofop_zip"} <= names


async def test_exofop_zip_needs_tag_or_username(tmp_path, monkeypatch):
    async with Client(mcp) as c:
        r = await c.call_tool("ph_exofop_zip", {"tic": "123456789", "initials": "eap", "source": "ogle"})
    assert "exofop_username" in r.content[0].text
