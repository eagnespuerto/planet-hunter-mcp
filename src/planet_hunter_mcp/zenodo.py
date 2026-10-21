"""Zenodo preprint bundle (prepared locally, never uploaded) and post-publish record checks (spec S12)."""
from __future__ import annotations

import csv
import json
import shutil
import tempfile
from pathlib import Path

from .util import http_get

LATEX_EXTS = {".tex", ".bib", ".bbl", ".pdf", ".png", ".eps", ".jpg"}


def _zip_files(files: list[Path], dest_zip: Path) -> Path:
    with tempfile.TemporaryDirectory() as td:
        for f in files:
            shutil.copy2(f, Path(td) / f.name)
        return Path(shutil.make_archive(str(dest_zip.with_suffix("")), "zip", td))


def build_bundle(*, tex_path: Path, code_files: list[Path], out_dir: Path, file_stem: str, title: str,
                 creators: list[dict], description: str, keywords: list[str], journal: str = "RNAAS",
                 dataset_dois: list[str], reference_lines: list[str], eclipse_rows: list[dict] | None = None,
                 cited_dois: list[str] | None = None, publication_date: str | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    job = tex_path.stem
    pdf = tex_path.with_suffix(".pdf")
    if not pdf.exists():
        raise FileNotFoundError(f"{pdf} not found: run ph_latex_build first.")
    made = []
    pre = out_dir / f"{file_stem}_preprint.pdf"
    shutil.copy2(pdf, pre)
    made.append(pre)
    from .manuscript import _flat_inputs
    src_files = [tex_path] + _flat_inputs(tex_path)
    made.append(_zip_files([p for p in src_files if p.exists()], out_dir / f"{file_stem}_latex_source.zip"))
    if code_files:
        made.append(_zip_files([p for p in code_files if p.exists()], out_dir / f"{file_stem}_code_and_data.zip"))
    if eclipse_rows:
        tp = out_dir / f"{file_stem}_eclipse_times.csv"
        with tp.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(eclipse_rows[0].keys()))
            w.writeheader()
            w.writerows(eclipse_rows)
        made.append(tp)

    desc = description if description.lower().startswith("preprint submitted") else \
        f"Preprint submitted to {journal}; not yet peer reviewed. {description}"
    related = [dict(identifier=d, relation="isDerivedFrom", resource_type="dataset", scheme="doi")
               for d in dataset_dois]
    related += [dict(identifier=d, relation="references", scheme="doi") for d in (cited_dois or [])]
    meta = {"metadata": {
        "upload_type": "publication", "publication_type": "preprint", "title": title,
        "creators": creators, "description": desc, "keywords": keywords, "license": "cc-by-4.0",
        "version": f"v1 (submitted to {journal})", "related_identifiers": related,
        "notes": "Code in the code-and-data archive is MIT licensed. Journal field intentionally blank until published.",
        **({"publication_date": publication_date} if publication_date else {}),
    }}
    (out_dir / "zenodo_metadata.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    (out_dir / "zenodo_references.txt").write_text("\n".join(reference_lines) + "\n", encoding="utf-8")
    upload_md = [
        f"# Zenodo upload walkthrough: {title}", "",
        "Nothing here has been uploaded. You click Publish.", "",
        "1. https://zenodo.org/uploads/new",
        "2. Files: " + ", ".join(p.name for p in made),
        "3. Resource type: Publication → Preprint",
        f"4. Title: {title}",
        "5. Creators: " + "; ".join(f"{c['name']}" + (f" (ORCID {c['orcid']})" if c.get("orcid") else "") for c in creators),
        f"6. Description: starts with “Preprint submitted to {journal}; not yet peer reviewed”",
        "7. License: CC BY 4.0 (code MIT, stated in Notes)",
        f"8. Version: v1 (submitted to {journal})",
        "9. Journal: leave BLANK until the paper is published",
        "10. Related works (Relation, Identifier, Scheme, Resource type):",
        *[f"    - {r['relation']}, {r['identifier']}, DOI, {r.get('resource_type', '')}" for r in related],
        "11. References: paste zenodo_references.txt (one per line)",
        "12. After publishing run ph_zenodo_check_record with the record ID and record BOTH the version DOI and "
        "the concept DOI.",
        "13. On acceptance: add the journal DOI as “Is previous version of” / “Is identical to”.",
    ]
    (out_dir / "ZENODO_UPLOAD.md").write_text("\n".join(upload_md) + "\n", encoding="utf-8")
    return dict(folder=str(out_dir), upload_files=[p.name for p in made],
                not_uploaded=["zenodo_metadata.json", "ZENODO_UPLOAD.md", "zenodo_references.txt"],
                metadata=meta["metadata"])


async def check_record(record_id: str, plan_path: Path | None = None) -> dict:
    """Fetch a published Zenodo record and diff it against zenodo_metadata.json."""
    rid = str(record_id).strip().rsplit("zenodo.", 1)[-1].rsplit("/", 1)[-1]
    rec = (await http_get(f"https://zenodo.org/api/records/{rid}")).json()
    md = rec.get("metadata", {})
    live = dict(doi=rec.get("doi") or md.get("doi"), conceptdoi=rec.get("conceptdoi"),
                title=md.get("title"), version=md.get("version"),
                creators=[c.get("name") for c in md.get("creators", [])],
                resource_type=md.get("resource_type"), license=(md.get("license") or {}).get("id"),
                related_identifiers=[(r.get("relation"), r.get("identifier")) for r in md.get("related_identifiers", [])],
                journal=md.get("journal"), files=[f.get("key") for f in rec.get("files", [])])
    diffs = []
    if plan_path and plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))["metadata"]
        if plan.get("title") != live["title"]:
            diffs.append(f"title: planned {plan.get('title')!r}, live {live['title']!r}")
        if plan.get("version") != live["version"]:
            diffs.append(f"version: planned {plan.get('version')!r}, live {live['version']!r}")
        pc = [c["name"] for c in plan.get("creators", [])]
        if pc != live["creators"]:
            diffs.append(f"creators: planned {pc}, live {live['creators']}")
        live_rel = {(r or "").lower() + "|" + (i or "").lower() for r, i in live["related_identifiers"]}
        for r in plan.get("related_identifiers", []):
            if f"{r['relation'].lower()}|{r['identifier'].lower()}" not in live_rel:
                diffs.append(f"missing related identifier: {r['relation']} {r['identifier']}")
        if live.get("journal"):
            diffs.append("journal field is filled; it should stay blank until publication")
    return dict(record=live, diffs=diffs, matches_plan=not diffs if plan_path else None,
                remember="Record both the version DOI and the concept DOI.")
