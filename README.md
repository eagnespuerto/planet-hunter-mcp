# Planet Hunter MCP

[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![release](https://img.shields.io/github/v/release/eagnespuerto/planet-hunter-mcp?include_prereleases&sort=semver)](https://github.com/eagnespuerto/planet-hunter-mcp/releases/latest)
[![tests](https://github.com/eagnespuerto/planet-hunter-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/eagnespuerto/planet-hunter-mcp/actions/workflows/tests.yml)
[![made with](https://img.shields.io/badge/made%20with-Python-3776AB?logo=python&logoColor=white)](https://github.com/eagnespuerto/planet-hunter-mcp)
[![MCP](https://img.shields.io/badge/MCP-server-6E56CF)](https://modelcontextprotocol.io)
[![website](https://img.shields.io/badge/website-live-brightgreen)](https://github.com/eagnespuerto/vetstar)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-support%20me-FF5E5B?logo=kofi&logoColor=white)](https://ko-fi.com/eagnespuerto)
[![Linktree](https://img.shields.io/badge/Linktree-soitsagnes-43E55E?logo=linktree&logoColor=white)](https://linktr.ee/soitsagnes)

An [MCP](https://modelcontextprotocol.io) server for TESS planet hunters. It connects an AI assistant to
[Vetstar](https://github.com/eagnespuerto/vetstar), MAST, SIMBAD, VizieR, Gaia DR3, ExoFOP-TESS, the NASA
Exoplanet Archive, NASA ADS, Crossref/DataCite, the Unified Astronomy Thesaurus and Zenodo. With those it
can take a candidate from a vetting report to a verified ephemeris, an AAS Research Note, and a Zenodo
preprint bundle that are ready to submit.

The tools follow [`AGENT_SPEC.md`](src/planet_hunter_mcp/AGENT_SPEC.md), the stage-by-stage contract (S1–S12)
taken from the HD 128329 (TIC 402175877) RNAAS run. The server **prepares files only**. It has no upload,
submit, publish or push tools; you click those buttons yourself.

## What it covers

| Stage | Tools | What they do |
|---|---|---|
| S1 Triage | `ph_vetstar_vet`, `ph_vetstar_parse_report`, `ph_vetstar_status`, `ph_quicklook_physics` | Run Vetstar on MAST data, or parse a Vetstar PDF with pymupdf. Red-flag checks catch BLS artefact periods, blind or zero-depth odd/even tests, placeholder depths, SPOC DV misfits, LS harmonics, R₂ > 2 R_Jup and centroid offsets. Also gives R₂ and a Planck-based T₂. |
| S2 Identity and contamination | `ph_resolve_target`, `ph_contamination`, `ph_simbad_region` | TIC v8.2 record. For each Gaia DR3 neighbour, the dip it would need, δ_nb = δ(1+f)/f, combined with the centroid σ. |
| S3 Novelty and literature | `ph_vizier_sweep`, `ph_vizier_query`, `ph_simbad_references`, `ph_exofop`, `ph_exoplanet_archive_cone`, `ph_exoplanet_archive_query`, `ph_ads_search` | Sweeps every VizieR catalogue and flags variability and period tables (this is how IJspeert+2024 turned up when SIMBAD missed it). Also ExoFOP TOIs and CTOIs, known planets and TOIs, and ADS through its API. |
| S4 Data | `ph_mast_list_lightcurves`, `ph_mast_download_lightcurves` | One light curve per sector, preferring SPOC 2-min, then TESS-SPOC FFI, then QLP. Cached locally, with warnings for artefact runs. |
| S5–S7 Analysis | `ph_find_eclipses`, `ph_fit_ephemeris`, `ph_primary_stack`, `ph_secondary_search` | Trapezoid mid-times flagged for full coverage. Weighted ephemeris with O−C. P/2, P/3 and 2P alias tests. Stacked primary (trapezoid, or with `model="batman"` a quadratic limb-darkened Mandel–Agol transit with Claret 2017 TESS limb-darkening priors: Rp/R★, b, a/R★, depth, T₁₄, T₂₃ with errors) and orbit geometry (k_min, a/R₁, b, e·sinω). Per-visit local-baseline secondary search with red-noise inflation and a lone-spike test. |
| S8 Figure | `ph_make_figure` | The single RNAAS figure: (a) stacked primary with model, (b) secondary search with a ±3σ band. |
| S9–S11 Manuscript | `ph_rnaas_scaffold`, `ph_rnaas_check`, `ph_latex_build`, `ph_rnaas_submission_folder` | AASTeX 7.0.1 skeleton with the AI disclosure, Vetstar credit and a `\dataset` entry for each MAST product. Checks RNAAS limits, compiles and renders pages, and builds a flat single-column submission folder that is tested standalone. |
| S10 References | `ph_verify_references`, `ph_uat_search` | Checks every DOI in a `.bib`/`.tex` against Crossref or DataCite (title, volume, page) and verifies UAT keyword IDs. |
| S12 Zenodo | `ph_zenodo_prepare_bundle`, `ph_zenodo_check_record` | Preprint PDF, source zip, code and data zip, deposit-format metadata and an upload walkthrough. After you publish, it diffs the live record against the plan. |
| Ledger and QA | `ph_paper_numbers`, `ph_acceptance_checklist` | Every quotable number is written to `paper_numbers.json`. The checklist reports the spec's §3 acceptance items. |

Resources: `planet-hunter://spec` (the pipeline contract) and `planet-hunter://targets/{tic}/numbers`.
Prompt: `vet_tess_candidate`.

## Validation

`scripts/regression_hd128329.py` reruns the published HD 128329 analysis through the MCP tools (Sector 93
QLP excluded, as in the paper):

| Quantity | Published | This server |
|---|---|---|
| T₀ (BJD_TDB) | 2459335.60643(36) | 2459335.60643(36) |
| P (d) | 39.295590(9) | 39.295590(9) |
| Primary depth / T₁₄ | 10.3 % / 7.1 h | 10.37 % / 7.09 h |
| Secondary at φ = 0.5 | −24 ± 79 ppm | −24 ± 79 ppm |
| 3σ limit, inflation | 240 ppm, 1.48 | 243 ppm, 1.48 |
| Brightest neighbour (G = 11.7 at 118″) | δ_nb ≈ 0.77 | δ_nb = 0.78 |

`scripts/regression_wasp98.py` is the known-planet control: WASP-98 b (TIC 166833457, TOI-404.01), with
34 transits in SPOC 2-min data over about 8 years:

| Check | Result |
|---|---|
| Vetstar S107 | "Known Planet", with 0 red flags |
| P | 2.9626414(3) d, against the archive's 2.96264036 d; O−C rms 0.95 min |
| TOI-404.01 epoch | Predicted to −0.5 min (0.9σ) at E = 255 |
| Contamination | On target. A G = 8.5 star 6.6 px away is ruled out by the on-target centroid. |
| Trapezoid fit | Depth 2.55 %, T₁₄ 1.84 h (underfits the limb-darkened bottom) |
| Limb-darkened fit (`model="batman"`) | Rp/R★ = 0.1587 ± 0.0011 (archive 0.15824 ± 0.00081, +0.3σ); b = 0.685 ± 0.018 (archive 0.71 ± 0.01, −1.2σ); depth 2.63 %, T₁₄ 1.92 h (archive 1.908 h) |

Archive values are fetched live with `ph_exoplanet_archive_query` (`pscomppars`) when the script runs.

Unit tests (`pytest -m "not network"`) use synthetic eclipsing-binary light curves and synthetic limb-darkened transits.

## Install

Requires Python ≥ 3.10. Building manuscripts also needs TeX Live with `aastex701.cls` and `aasjournalv7.bst`.

```bash
git clone https://github.com/eagnespuerto/planet-hunter-mcp
```

```bash
cd planet-hunter-mcp && python -m venv .venv && .venv/bin/pip install -e .
```

On Windows the executable is at `.venv\Scripts\planet-hunter-mcp.exe`.

### Claude Code

```bash
claude mcp add planet-hunter -- /path/to/planet-hunter-mcp/.venv/bin/planet-hunter-mcp
```

### Claude Desktop / other clients (`mcpServers` JSON)

```json
{
  "mcpServers": {
    "planet-hunter": {
      "command": "/path/to/planet-hunter-mcp/.venv/bin/planet-hunter-mcp",
      "env": { "PLANET_HUNTER_WORKDIR": "~/planet-hunter", "ADS_API_TOKEN": "" }
    }
  }
}
```

Streamable HTTP, for remote use: `planet-hunter-mcp --transport streamable-http --port 8000`.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PLANET_HUNTER_WORKDIR` | `~/planet-hunter` | Per-target cache and outputs (`TIC<id>/lc`, `paper_numbers.json`, `paper/`) |
| `VETSTAR_URL` | `https://vetstar.onrender.com` | Vetstar API. Point it at a local instance (`http://localhost:8000`) if you run your own. |
| `VETSTAR_TIMEOUT` | `600` | Seconds. The free tier cold-starts, and MAST downloads happen server-side. |
| `ADS_API_TOKEN` | unset | Needed for `ph_ads_search`; get one from <https://ui.adsabs.harvard.edu/user/settings/token>. |

## Ground rules built into the tools

- Nothing is submitted, uploaded or published. Bundles and walkthroughs are prepared, and a human acts on them.
- Bot challenges are never bypassed. Without an ADS token the server explains what to do and does not
  scrape the UI.
- Every DOI is resolved before it is cited. A DOI written from memory resolved to a different paper in the
  reference run.
- Every number has a source. Analysis tools write to `paper_numbers.json`, and the scaffolded `.tex` embeds
  that ledger as comments.
- Novelty claims come after the literature sweep. VizieR's all-catalogue sweep is part of S3 because
  SIMBAD alone is not enough.

## Development

```bash
pip install -e ".[dev]" && pytest -m "not network"
```

Credits: Vetstar (TESS Vetting Studio) by Ella Agnes Puerto. Data services: MAST/TESS, CDS SIMBAD and VizieR,
ESA Gaia, ExoFOP-TESS, the NASA Exoplanet Archive, NASA ADS, Crossref, DataCite, the UAT and Zenodo.

Built with help from Claude (Anthropic). MIT licensed.
