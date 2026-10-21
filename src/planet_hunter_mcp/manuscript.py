"""AASTeX 7 Research Note scaffolding, limit checks, LaTeX builds and the RNAAS folder (spec S8–S9, S11)."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import MAST_DATASET_DOIS

RNAAS_LIMITS = dict(abstract_words=150, body_words=1000, figures_plus_tables=1, references=15)

ACK_BOILERPLATE = r"""This paper includes data collected by the \tess\ mission, which are publicly available from the
Mikulski Archive for Space Telescopes (MAST). Funding for the \tess\ mission is provided by
NASA's Science Mission Directorate. This research made use of the SIMBAD database and the VizieR
catalogue access tool, operated at CDS, Strasbourg, France, and of the International Variable
Star Index (VSX) operated by the AAVSO. This work has made use of data from the European Space
Agency (ESA) mission Gaia, processed by the Gaia Data Processing and Analysis Consortium (DPAC)."""


def _tex_escape(s: str) -> str:
    return re.sub(r"([%&#_$])", r"\\\1", s)


def scaffold_rnaas(out_dir: Path, job: str, *, title: str, author: str, orcid: str | None, affiliation: str,
                   email: str, star_label: str, keywords: list[tuple[str, int]], pipelines: list[str],
                   numbers: dict, vetstar_version: str = "", ai_tool: str = "Claude (Anthropic), via Claude Code",
                   short_title: str | None = None, twocolumn: bool = True) -> dict:
    """Write <job>.tex and an empty <job>.bib. Prose is left as %% TODO blocks for the author/agent."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tex, bib = out_dir / f"{job}.tex", out_dir / f"{job}.bib"
    fig = f"{job}_fig1.pdf"  # must differ from the job name (pdfTeX would embed its own output)
    eph = numbers.get("ephemeris", {})
    ephem_line = ""
    if eph.get("T0_bjd_tdb") and eph.get("P"):
        ephem_line = (r"\begin{equation}" "\n"
                      rf"T_\mathrm{{min}} = \mathrm{{BJD_{{TDB}}}}\,{eph['T0_bjd_tdb']:.5f}({eph['e_T0'] * 1e5:.0f}) "
                      rf"+ {eph['P']:.6f}({eph['e_P'] * 1e6:.0f})\,E," "\n" r"\end{equation}")
    ledger = "\n".join(f"%%   {sec}: " + ", ".join(f"{k}={v}" for k, v in vals.items()
                                                    if not isinstance(v, (list, dict)))[:400]
                       for sec, vals in numbers.items() if isinstance(vals, dict))
    datasets = ",\n".join(
        rf"\dataset[doi:{MAST_DATASET_DOIS[p]}]{{https://doi.org/{MAST_DATASET_DOIS[p]}}}"
        for p in list(dict.fromkeys(pipelines + ["TIC"])) if p in MAST_DATASET_DOIS)
    kw = " --- ".join(rf"\uat{{{n}}}{{{i}}}" for n, i in keywords) or r"\uat{TODO}{0}"
    vet = f"Vetstar{(' ' + vetstar_version) if vetstar_version else ''} (\\url{{https://github.com/eagnespuerto/vetstar}})"
    author_cmd = rf"\author[{orcid}]{{{author}}}" if orcid else rf"\author{{{author}}}"
    body = rf"""%% {star_label} -- Research Note (RNAAS) / preprint, scaffolded by planet-hunter-mcp
%% Build: pdfLaTeX -> BibTeX -> pdfLaTeX -> pdfLaTeX   (or ph_latex_build)
%% RNAAS limits: abstract <= 150 words, body <= 1000 words, 1 figure or table, <= 15 references.
%% Every number in the text must come from paper_numbers.json. Ledger at scaffold time:
{ledger}
\documentclass{"[twocolumn]" if twocolumn else ""}{{aastex701}}

\newcommand{{\tess}}{{\textit{{TESS}}}}

\shorttitle{{{_tex_escape(short_title or title)[:60]}}}
\shortauthors{{{author.split()[-1]}}}
\submitjournal{{RNAAS}}

\begin{{document}}

\title{{{title}}}

{author_cmd}
\affiliation{{{affiliation}}}
\email{{{email}}}

\begin{{abstract}}
%% TODO: <=150 words. Target, data span, ephemeris, depth/duration, alias exclusion, secondary limit, interpretation.
\end{{abstract}}

\keywords{{{kw}}}

\section{{Introduction}}
%% TODO: identity (spectral type, TIC parameters), prior literature from the VizieR sweep / SIMBAD / ExoFOP,
%% scoped novelty statement, and credit for the vetting tool that flagged it:
%% "... flagged as an eclipsing-binary candidate with the {vet} vetting tool."

\section{{Data and Analysis}}

%% Defined at the start of Section 2 so the two-column figure* floats to the top of the next page.
\begin{{figure{"*" if twocolumn else ""}}}[t]
\centering
\includegraphics[width={"\\textwidth" if twocolumn else "\\columnwidth"}]{{{fig}}}
\caption{{%% TODO (a) stacked, locally normalized primary eclipse with model; (b) secondary search with $\pm3\sigma$ band.
}}
\label{{fig:lc}}
\end{{figure{"*" if twocolumn else ""}}}

%% TODO: sectors and pipelines (SPOC 2-min > TESS-SPOC FFI > QLP), rejected sectors and why, contamination numbers.
{ephem_line}
%% TODO: O-C rms, alias exclusion counts (P/2, P/3), 2P test, primary depth/T14/T23, k_min and R2,
%% secondary method (per-visit local baseline), inflation factor, depth at phase 0.5, 3-sigma limit, lone spike.

\section{{Discussion}}
%% TODO: expected circular-orbit secondary vs limit, eccentric-orbit inference (e sin w), alternatives, follow-up.

\begin{{acknowledgments}}
{ACK_BOILERPLATE}

\textit{{AI disclosure:}} The author used the generative AI assistant {ai_tool} %% TODO: model/version
to %% TODO: what it did (query catalogs, write analysis scripts, draft text).
The author reviewed and verified all analysis steps, results, references, and text, and takes full
responsibility for the content of this note.
\end{{acknowledgments}}

\facilities{{TESS}}
\software{{{vet},
planet-hunter-mcp (\url{{https://github.com/eagnespuerto/planet-hunter-mcp}}),
lightkurve, astropy, astroquery, NumPy, SciPy, Matplotlib}}

\noindent Data: {datasets}.

\bibliography{{{job}}}{{}}
\bibliographystyle{{aasjournalv7}}

\end{{document}}
"""
    tex.write_text(body, encoding="utf-8")
    if not bib.exists():
        bib.write_text("% Add only entries whose DOI passed ph_verify_references.\n", encoding="utf-8")
    return dict(tex=str(tex), bib=str(bib), figure_expected=str(out_dir / fig),
                dataset_dois=[MAST_DATASET_DOIS[p] for p in dict.fromkeys(pipelines + ["TIC"]) if p in MAST_DATASET_DOIS],
                next_steps=["Fill every %% TODO with numbers from paper_numbers.json",
                            "Verify every DOI (bib + \\dataset) with ph_verify_references",
                            "Verify UAT IDs with ph_uat_search", "ph_rnaas_check then ph_latex_build"])


# ------------------------------------------------------------------------------ checks

def _strip_tex(s: str) -> str:
    s = re.sub(r"(?<!\\)%.*", "", s)
    s = re.sub(r"\\begin\{(equation|figure\*?|table\*?)\}.*?\\end\{\1\}", " ", s, flags=re.S)
    s = re.sub(r"\$[^$]*\$", " X ", s)
    s = re.sub(r"\\(cite[pt]?|ref|label|url|dataset)(\[[^\]]*\])?\{[^}]*\}", " X ", s)
    s = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?", " ", s)
    s = re.sub(r"[{}~\\]", " ", s)
    return s


def _words(s: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][\w'.\-–]*", _strip_tex(s)))


def check_rnaas(tex_path: Path) -> dict:
    src = tex_path.read_text(encoding="utf-8")
    job = tex_path.stem
    nocomment = re.sub(r"(?<!\\)%.*", "", src)
    m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", nocomment, re.S)
    abstract_words = _words(m.group(1)) if m else 0
    b0 = nocomment.find(r"\section")
    b1 = nocomment.find(r"\begin{acknowledgments}")
    body_words = _words(nocomment[b0:b1 if b1 > 0 else None]) if b0 >= 0 else 0
    figs = len(re.findall(r"\\begin\{figure\*?\}", nocomment))
    tabs = len(re.findall(r"\\begin\{(deluxe)?table\*?\}", nocomment))
    cites = set()
    for g in re.findall(r"\\cite[a-z]*\*?(?:\[[^\]]*\])*\{([^}]*)\}", nocomment):
        cites.update(k.strip() for k in g.split(","))
    graphics = re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]*)\}", nocomment)
    checks = {
        "abstract_words<=150": abstract_words <= 150 and abstract_words > 0,
        "body_words<=1000": body_words <= 1000,
        "figures+tables<=1": figs + tabs <= 1,
        "references<=15": len(cites) <= 15,
        "email_present": r"\email{" in nocomment,
        "ai_disclosure_present": bool(re.search(r"AI disclosure", nocomment, re.I)),
        "facilities_present": r"\facilities{" in nocomment,
        "software_present": r"\software{" in nocomment,
        "dataset_dois_present": r"\dataset[" in nocomment,
        "originating_tool_credited": "vetstar" in nocomment.lower(),
        "figure_name_differs_from_job": all(Path(g).stem != job for g in graphics),
        "no_TODO_left": "TODO" not in src,
        "submitjournal_RNAAS": r"\submitjournal{RNAAS}" in nocomment,
    }
    if "[twocolumn]" in nocomment and figs:
        # A two-column figure* only floats to the top of a later page: define it before the last
        # section, otherwise it lands inside the reference list.
        fpos = nocomment.find(r"\begin{figure")
        secs = [mm.start() for mm in re.finditer(r"\\section\{", nocomment)]
        checks["figure*_defined_early"] = len(secs) < 2 or fpos < secs[-1]
    return dict(tex=str(tex_path), abstract_words=abstract_words, body_words=body_words, figures=figs,
                tables=tabs, references_cited=len(cites), cite_keys=sorted(cites), graphics=graphics,
                checks=checks, passed=all(checks.values()),
                failed=[k for k, v in checks.items() if not v], limits=RNAAS_LIMITS)


# ------------------------------------------------------------------------------ builds

def _run(cmd: list[str], cwd: Path, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def latex_build(tex_path: Path, use_bibtex: bool = True, render_dpi: int = 70) -> dict:
    """pdfLaTeX → BibTeX → pdfLaTeX ×2; fail on '^! ' or 'undefined' in the log; render pages to PNG."""
    if not shutil.which("pdflatex"):
        return dict(ok=False, error="pdflatex not on PATH. Install TeX Live (needs aastex701.cls, aasjournalv7.bst).")
    cwd, job = tex_path.parent, tex_path.stem
    steps = [["pdflatex", "-interaction=nonstopmode", job]]
    if use_bibtex:
        steps.append(["bibtex", job])
    steps += [["pdflatex", "-interaction=nonstopmode", job]] * 2
    for cmd in steps:
        _run(cmd, cwd)
    log = (cwd / f"{job}.log").read_text(encoding="utf-8", errors="replace") if (cwd / f"{job}.log").exists() else ""
    problems = [ln for ln in log.splitlines() if re.match(r"^! ", ln) or "undefined" in ln]
    pdf = cwd / f"{job}.pdf"
    pages = []
    if pdf.exists():
        from .vetstar import render_pages
        pages = render_pages(pdf, cwd / "_pages", dpi=render_dpi)
    return dict(ok=pdf.exists() and not problems, pdf=str(pdf) if pdf.exists() else None,
                problems=problems[:30], n_pages=len(pages), page_images=pages,
                next_step="Open the page images and inspect layout (figure placement, references, overflow).")


def _flat_inputs(tex_path: Path) -> list[Path]:
    src = re.sub(r"(?<!\\)%.*", "", tex_path.read_text(encoding="utf-8"))
    files = [tex_path.with_suffix(".bbl")]
    for group in re.findall(r"\\bibliography\{([^}]*)\}", src):
        files += [tex_path.parent / (b.strip() if b.strip().endswith(".bib") else b.strip() + ".bib")
                  for b in group.split(",")]
    for g in re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]*)\}", src):
        p = tex_path.parent / g
        if not p.suffix:
            p = next((p.with_suffix(e) for e in (".pdf", ".png", ".jpg", ".eps") if p.with_suffix(e).exists()), p)
        files.append(p)
    return [f for f in files if f.exists()]


def build_submission(tex_path: Path, out_dir: Path | None = None, single_column: bool = True) -> dict:
    """Flat RNAAS folder (.tex, .bib, .bbl, figure, .pdf), single column without line numbers,
    then compile it standalone in a clean temp dir both with the shipped .bbl and with fresh BibTeX."""
    out = out_dir or tex_path.parent / "submission"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    src = tex_path.read_text(encoding="utf-8")
    if single_column:
        src = re.sub(r"\\documentclass\[[^\]]*twocolumn[^\]]*\]\{aastex701\}", r"\\documentclass{aastex701}", src)
        src = src.replace(r"\begin{figure*}", r"\begin{figure}").replace(r"\end{figure*}", r"\end{figure}")
        src = src.replace(r"width=\textwidth", r"width=\columnwidth")
        src = re.sub(r"^\\linenumbers\s*$", "", src, flags=re.M)
    (out / tex_path.name).write_text(src, encoding="utf-8")
    for f in _flat_inputs(tex_path):
        if f.suffix != ".bbl":
            shutil.copy2(f, out / f.name)
    build = latex_build(out / tex_path.name)
    for ext in (".aux", ".blg", ".log", ".out"):
        (out / f"{tex_path.stem}{ext}").unlink(missing_ok=True)
    shutil.rmtree(out / "_pages", ignore_errors=True)
    tests = {}
    for mode in ("shipped_bbl", "fresh_bibtex"):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            for f in out.iterdir():
                if f.name != f"{tex_path.stem}.pdf":  # everything except the compiled output
                    shutil.copy2(f, tdp / f.name)
            r = latex_build(tdp / tex_path.name, use_bibtex=(mode == "fresh_bibtex"), render_dpi=30)
            tests[mode] = dict(ok=r["ok"], problems=r.get("problems", [])[:5])
    files = sorted(p.name for p in out.iterdir())
    return dict(folder=str(out), files=files, flat=all(p.is_file() for p in out.iterdir()),
                build_ok=build["ok"], build_problems=build.get("problems"), standalone_tests=tests,
                reminders=["RNAAS asked for no line numbers (single column default).",
                           "Ship .bib and .bbl regenerated together.",
                           "A public named preprint is incompatible with dual-anonymous review: opt out.",
                           "The human submits; this tool never uploads."])
