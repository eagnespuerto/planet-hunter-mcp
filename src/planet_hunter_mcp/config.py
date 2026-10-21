"""Runtime configuration, read from environment variables."""
from __future__ import annotations

import os
import re
from pathlib import Path

VETSTAR_URL = os.environ.get("VETSTAR_URL", "https://vetstar.onrender.com").rstrip("/")
ADS_API_TOKEN = os.environ.get("ADS_API_TOKEN", "")
WORKDIR = Path(os.environ.get("PLANET_HUNTER_WORKDIR", Path.home() / "planet-hunter")).expanduser()

USER_AGENT = "planet-hunter-mcp/0.1 (+https://github.com/eagnespuerto/planet-hunter-mcp)"
HTTP_TIMEOUT = float(os.environ.get("PLANET_HUNTER_HTTP_TIMEOUT", "60"))
VETSTAR_TIMEOUT = float(os.environ.get("VETSTAR_TIMEOUT", "600"))  # free tier cold-starts + MAST downloads

BTJD_OFFSET = 2457000.0

# MAST HLSP / mission DOIs that the spec says to cite. They must still be checked with
# ph_verify_references before a manuscript is submitted.
MAST_DATASET_DOIS = {
    "SPOC": "10.17909/t9-nmc8-f686",
    "TESS-SPOC": "10.17909/t9-wpz1-8s54",
    "QLP": "10.17909/t9-r086-e880",
    "TIC": "10.17909/fwdt-2x66",
}


def normalize_tic(tic: str | int) -> int:
    """Accept 402175877, 'TIC 402175877', 'tic402175877' → 402175877."""
    m = re.search(r"(\d{3,})", str(tic))
    if not m:
        raise ValueError(f"Could not read a TIC ID from {tic!r}; pass digits like 402175877.")
    return int(m.group(1))


def target_dir(tic: str | int) -> Path:
    """Per-target working directory where light curves and outputs are cached."""
    d = WORKDIR / f"TIC{normalize_tic(tic)}"
    d.mkdir(parents=True, exist_ok=True)
    return d
