# AGENT SPEC — TESS eclipsing-binary candidate: vetting report → refined ephemeris → RNAAS + Zenodo preprint

Spec version 1.0 · 2026-10-21 · Derived from the HD 128329 (TIC 402175877) run.
Reference implementation: `../hd128329_period/` (`period_alias.py`, `refine.py`, `secondary.py`,
`paper_numbers.py`, `build_release.sh`). Companion human guide: `EB_Vetting_Field_Guide.pdf`.

---

## 0. Scope and contract

**Input:** a single-target vetting report (e.g. Vetstar PDF) flagging a TESS target as a transit /
eclipsing-binary candidate, plus a TIC ID.
**Output (all optional, in order):** (a) verdict: planet / EB on target / contamination;
(b) multi-sector ephemeris + alias exclusion; (c) secondary-eclipse limit + orbit interpretation;
(d) AASTeX 7 Research Note; (e) RNAAS submission folder; (f) Zenodo preprint bundle.

**Authority rules (non-negotiable):**
- Never submit, upload, publish, push, or open a PR without an explicit user instruction for that
  specific action. Prepare files; the human clicks Publish/Submit.
- Never kill processes you did not start without the user's explicit instruction. Report them.
- Never bypass bot detection (CAPTCHA, ADS 202 challenge pages). Fall back or ask the user.
- Never state a DOI, catalogue value, or "first detection" claim you have not verified this run.
- Correct your own earlier claims explicitly when new evidence contradicts them.

---

## 1. Environment (Windows lessons; generalize as needed)

| Issue | Rule |
|---|---|
| Console is cp1252 | Run Python with `-X utf8`; write text output to files with `encoding="utf-8"`. |
| `python -I` hides user site-packages | Use `-P` (no cwd on `sys.path`) for scripts that live in your own dir; keep downloads in a separate dir. |
| Read tool reports PDF "password-protected" / needs pdftoppm | Not trustworthy. Extract with `pymupdf` (`page.get_text()`, `page.get_pixmap(dpi=90)`). |
| Piping long jobs through `grep` | Buffers output → looks hung. Use `python -u ... > log.txt 2>&1` and watch the log. |
| `sed`/heredoc with LaTeX backslashes | Escaping silently fails. Edit `.tex` with a file-edit tool or a Python script file, then `grep` to confirm. |
| CPU contention | Before long jobs, list processes + `% Processor Time`. If other users'/sessions' jobs saturate cores, report them (name, PID, start time, parent) and ask; do not kill. |
| Install | `pip install pymupdf astroquery lightkurve scipy matplotlib`; LaTeX: TeX Live with `aastex701.cls`, `aasjournalv7.bst`. |

---

## 2. Pipeline

Each stage: **Do → Decide → Gate** (gate = condition that must hold before the next stage).

### S1. Triage the vetting report
- **Do:** extract all text + render pages. Record: TIC, sector, Tmag, Teff, R★, CROWDSAP, dip
  table (times, depths, durations), BLS/LS periods, odd/even, secondary, centroid, model fits.
- **Decide — red flags that the report is unreliable:**
  - Only 1 discrete dip but a BLS period with "2 transits in window" → period is an artifact.
  - Odd/even "mismatch" where one depth ≈ 0 → wrong period, *not* an EB indicator.
  - Odd/even computed only over primaries of an alternating-depth series → test is blind.
  - `depth_ppm = 1e6`, sigma fields contradicting the vetting-test page → placeholder/broken features.
  - SPOC DV depth ≠ measured dip depth by >×1.5 → planet model misfit (EB).
  - LS top period = P/n → harmonic, ignore.
- **Physics quick-look:** R₂ ≈ √(depth/CROWDSAP-corrected) × R★. R₂ > ~2 R_Jup → not a planet.
  Alternating depths with secondary at phase 0.5 → EB. Depth ratio ≈ surface-brightness ratio →
  T₂ via Planck in 600–1000 nm.
- **Gate:** target identified (coords), dip list extracted.

### S2. Catalogue identity + contamination
- **Do:** `astroquery` SIMBAD region 30″ & 150″ (fields: `otype, sp, V, G, plx, ids`; fetch
  `otypes` separately — joining it duplicates rows); SIMBAD TAP references
  (`ref b JOIN has_ref h ON b.oidbib=h.oidbibref JOIN ident i ON i.oidref=h.oidref`);
  VizieR: `B/vsx/vsx` 150″, `I/355/gaiadr3` 63″, `IV/39/tic82` 63″, Gaia `I/358/*` variability.
- **Decide (contamination):** for each neighbour, f = F_nb/F_target = 10^(−0.4 ΔG). To produce
  the observed fractional dip δ, the neighbour itself must dim by δ_nb = δ·(1+f)/f. If δ_nb > 1
  → impossible; if δ_nb > ~0.5 at multi-pixel separation with no centroid shift → excluded.
  Combine with centroid offset < 3σ. (HD 128329: G=11.7 neighbour at 118″, f≈0.15 → δ_nb≈0.77.)
- **Gate:** "on target" or "contaminant identified" stated with numbers.

### S3. Novelty / literature (do this BEFORE claiming anything new)
- **Do:** (1) VizieR **all-catalogue** position query (radius 3″, `row_limit` small) → list every
  table containing the star; flag tables whose description matches
  `var|binar|eclips|period|tess|kepler|asas|photom|light|rot|puls`; then read those rows in full.
  (2) ExoFOP JSON: `https://exofop.ipac.caltech.edu/tess/target.php?id=<TIC>&json` → TOIs/CTOIs.
  (3) Web search. (4) ADS: needs user token (`ADS_API_TOKEN`) — scripted access to the UI/core
  returns HTTP 202 bot challenge → do not bypass; ask the user to run the final ADS query.
- **Lesson:** SIMBAD missed IJspeert+2024 (A&A 691 A242), which already listed P = 39.2486 d.
  The VizieR sweep found it. Novelty claims must be re-scoped accordingly.
- **Gate:** written list of prior periods/epochs/eccentricities with sources.

### S4. Multi-sector data
- **Do:** `lk.search_lightcurve(TIC)`; per sector prefer SPOC 2-min > TESS-SPOC FFI > QLP.
  `quality_bitmask="default"`, `pdcsap_flux` if finite, `remove_nans().normalize()`.
- **Decide:** reject points/events with depth > 0.5 when the real eclipse is ~0.1 (QLP produced
  near-zero-flux runs that looked like 99 % eclipses). Record per-sector time ranges.
- **Gate:** table of sectors (author, cadence, BTJD range).

### S5. Eclipse finding + timing
- **Do:** 30-min bins; dips = bins below (1 − 0.03) × median; group; mid-time by fitting a
  trapezoid (t0, depth, T14, T23) within ±0.6 d. Flag `fully_covered` = data on both sides of
  ingress/egress.
- **Decide:** use only fully covered events for the ephemeris. Partial events: their flux-weighted
  centroids are biased (seen: −113 and −74 min). Validate them instead by evaluating the stacked
  model *at the predicted time* (rms resid ≪ model-shifted rms ⇒ consistent).
- **Gate:** ≥ 3 fully covered eclipses spanning > 1 TESS season.

### S6. Ephemeris + aliases
- **Do:** integer epochs from a trial P (ratio of separations); weighted linear fit → T0, P, σ;
  O−C list. Convert BTJD → BJD_TDB (+2457000).
- **Alias tests:** for P/2, P/3: count predicted mid-times with data (±0.04 d) and no dip → any
  such → excluded. For 2P: compare even vs odd epoch depths within the *same pipeline* (FFI vs
  2-min offsets differ by ~1 %).
- **Performance:** never loop predicted epochs over the full unbinned array (O(N²), hours). Bin to
  10 min, `np.searchsorted` vectorized per trial period.
- **Gate:** O−C rms ≪ eclipse duration; all shorter aliases excluded with counts.

### S7. Secondary eclipse + interpretation
- **Do:** per-visit local baseline: depth = median(baseline 2.5–6 h either side) − median(±1.5 h
  window), per orbit, weighted mean per trial phase (grid 0.001). Errors from baseline scatter;
  inflate by robust SNR scatter (1.4826·MAD of SNR across phases; seen 1.48) for red noise.
- **Never** use a global median baseline: sector-to-sector offsets masquerade as 17σ "secondaries".
- **Decide:** report depth at 0.5, max excursion (and whether it is a lone spike), 3σ limit.
  Expected circular-orbit secondary ≈ δ_pri × B_λ(T₂)/B_λ(T₁) integrated over 600–1000 nm.
  k_min = √δ ⇒ R₂ ≥ k_min R₁. Duration check: T14(b=0) = P/π·(1+k)/(a/R₁); T_obs ≪ that ⇒
  grazing (if circular). No secondary + large R₂ ⇒ eccentric (non-eclipsing secondary
  conjunction): e sinω ≳ ((1+k)/b − 1)/((1+k)/b + 1). State alternatives (hierarchical triple).
- **Gate:** every number traceable to a script output (`paper_numbers.json`).

### S8. Figure
- One figure (RNAAS limit): (a) locally normalized stacked primary + model; (b) secondary
  search ±3σ band + expected-depth reference line (put off-scale values in a label).
- **File name must differ from the LaTeX job name** (`fig1.pdf` vs `note.pdf`) — otherwise pdfTeX
  embeds the file it is writing → "xpdf: reading PDF image failed".

### S9. Manuscript (AASTeX 7.0.1)
- `\documentclass[twocolumn]{aastex701}` for the preprint. AASTeX 7 has **no RNAAS option**.
- RNAAS limits: abstract ≤ 150 words, body ≤ 1000, 1 figure or table, ≤ 15 references.
- Author block: `\author[ORCID]{Name}`, `\affiliation{..., City, Country}`, `\email{}` (required;
  printed only with `[show]`). `\submitjournal{RNAAS}` prints "Submitted to RNAAS" under the
  affiliation.
- Twocolumn `figure*` floats only to the top of a *later* page → define it near the start of
  Section 2, not at the end (else it lands inside the reference list).
- Required sections: acknowledgments (TESS/MAST, SIMBAD/VizieR, VSX, Gaia boilerplate),
  **AI disclosure** (tool, model, what it did, author verified + responsible), `\facilities`,
  `\software` (credit originating tools, e.g. Vetstar URL), `\dataset[doi:…]` for **every** MAST
  product touched (SPOC 10.17909/t9-nmc8-f686, TESS-SPOC 10.17909/t9-wpz1-8s54, QLP
  10.17909/t9-r086-e880, TIC 10.17909/fwdt-2x66 — verify each via DataCite).
- UAT concepts: verify IDs from `https://raw.githubusercontent.com/astrothesaurus/UAT/master/UAT.json`.
  Avoid exoplanet-branch concepts for EB papers.
- **Gate:** clean build (pdfLaTeX → BibTeX → pdfLaTeX ×2), 0 `^! |undefined` in log, word/abstract
  counts within limits, render pages and inspect.

### S10. Reference verification
- Every DOI → `https://api.crossref.org/works/<doi>` (journals) or
  `https://api.datacite.org/dois/<doi>` (data). Compare title/volume/page. A DOI written from
  memory resolved to a *different paper* in this run — never skip this.

### S11. RNAAS submission folder
- Flat directory (no subfolders): `.tex`, `.bib`, `.bbl`, figure, compiled `.pdf`.
- **Line numbers:** general AAS checklist asks for them; **RNAAS asked to remove them.** Default:
  `\documentclass{aastex701}` (single column, no line numbers) for RNAAS.
- Standalone test: copy folder to a clean dir, compile with shipped `.bbl` and with fresh BibTeX.
- `.bib` = source database; `.bbl` = BibTeX output read by LaTeX. Ship both, regenerated together.
- Dual-anonymous review is incompatible with a public named preprint → advise opting out.

### S12. Zenodo preprint bundle
- Files: preprint PDF, LaTeX source zip, code+data zip (scripts, JSON outputs, CSV, README),
  timing CSV. Plus (not uploaded): `zenodo_metadata.json` (deposit API format),
  `ZENODO_UPLOAD.md` (form walkthrough), `zenodo_references.txt` (one citation per line).
- Metadata: Resource type Publication→Preprint; description starts "Preprint submitted to RNAAS;
  not yet peer reviewed"; license CC BY 4.0 (code MIT noted in Notes); **Journal field blank until
  published**; version "v1 (submitted to RNAAS)".
- Related works schema: `{Relation, Identifier, Scheme, Resource type}`; API (InvenioRDM):
  `{"identifier","scheme","relation_type":{"id":"isderivedfrom"},"resource_type":{"id":"dataset"}}`.
  Datasets → *Is derived from*; cited papers/software → *References*; on acceptance add the
  journal DOI as *Is previous version of* / *Is identical to*.
- After publish: fetch `https://zenodo.org/api/records/<id>` and diff against the plan (creator
  name, related identifiers, version were missing on first publish in this run). Record both the
  version DOI and the concept DOI (`conceptdoi`).
- Minor file fixes may be possible in place (user confirmed for this record); otherwise new version.

---

## 3. Acceptance checklist (agent must report each as pass/fail with evidence)

- [ ] Verdict with contamination numbers and centroid σ
- [ ] Prior-literature table (incl. VizieR sweep) and adjusted novelty statement
- [ ] Ephemeris with σ, O−C, epochs; aliases P/2, P/3 excluded (counts); 2P tested
- [ ] Secondary: depth@0.5, 3σ limit (with inflation factor), lone-spike assessment
- [ ] Every paper number in `paper_numbers.json`; every DOI verified
- [ ] Clean LaTeX builds (preprint + submission), limits met, pages visually inspected
- [ ] AI disclosure + originating-tool credit present
- [ ] Nothing published/submitted by the agent

## 4. Worked-example values (HD 128329)

| Item | Value |
|---|---|
| Sectors | 12, 38, 39, 65, 66, 93 (QLP, excluded), 99–102 |
| Ephemeris | BJD_TDB 2459335.60643(36) + 39.295590(9) E |
| Prior period | 39.2486 d (IJspeert+2024; off by 0.047 d → 2.2 d drift by E=44) |
| Primary | δ = 10.3 %, T14 = 7.1 h, T23 = 1.4 h |
| Secondary | −24 ± 79 ppm at φ=0.5; 3σ < 240 ppm (inflation 1.48) |
| Inference | R₂ ≳ 0.93 R☉; circular would give ~1.5 % secondary → eccentric, e sinω ≳ 0.06 |
| Outputs | RNAAS submitted 2026-10-21; Zenodo doi:10.5281/zenodo.23512468 |
