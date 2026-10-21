"""Reference and keyword verification (spec S9–S10): Crossref, DataCite, UAT."""
from __future__ import annotations

import difflib
import re

import httpx

from .util import http_get

UAT_URL = "https://raw.githubusercontent.com/astrothesaurus/UAT/master/UAT.json"
_uat_cache: list[dict] | None = None


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


async def verify_doi(doi: str, expect_title: str | None = None, expect_volume: str | None = None,
                     expect_page: str | None = None) -> dict:
    """Resolve a DOI at Crossref (journals), falling back to DataCite (data/software/Zenodo)."""
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi.strip(), flags=re.I)
    rec: dict = {"doi": doi}
    try:
        m = (await http_get(f"https://api.crossref.org/works/{doi}")).json()["message"]
        title = " ".join([(m.get("title") or [""])[0]] + [x for x in (m.get("subtitle") or [])][:1])
        rec.update(registry="crossref", title=re.sub(r"<[^>]+>|\s+", " ", title).strip(),
                   container=(m.get("container-title") or [""])[0], volume=m.get("volume"),
                   page=m.get("page"), article_number=m.get("article-number"),
                   year=((m.get("issued") or {}).get("date-parts") or [[None]])[0][0],
                   first_author=((m.get("author") or [{}])[0]).get("family"), type=m.get("type"))
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 404:
            raise
        try:
            a = (await http_get(f"https://api.datacite.org/dois/{doi}")).json()["data"]["attributes"]
        except httpx.HTTPStatusError as e2:
            if e2.response.status_code == 404:
                return {**rec, "resolves": False, "ok": False,
                        "message": "DOI not registered at Crossref or DataCite: do not cite it."}
            raise
        rec.update(registry="datacite", title=(a.get("titles") or [{}])[0].get("title"),
                   container=a.get("publisher") if isinstance(a.get("publisher"), str)
                   else (a.get("publisher") or {}).get("name"),
                   year=a.get("publicationYear"), type=(a.get("types") or {}).get("resourceTypeGeneral"),
                   first_author=((a.get("creators") or [{}])[0]).get("name"), version=a.get("version"))
    rec["resolves"] = True
    problems, warnings = [], []
    sim = None
    if expect_title:
        a, b = _norm(expect_title), _norm(rec.get("title"))
        sim = difflib.SequenceMatcher(None, a, b).ratio()
        if a and b and (a.startswith(b) or b.startswith(a)):  # subtitle present on only one side
            sim = max(sim, 0.95)
        rec["title_similarity"] = round(sim, 3)
        if sim < 0.8:
            problems.append(f"title mismatch (similarity {sim:.2f}): DOI points to “{rec.get('title')}”")
    if expect_volume and str(expect_volume) != str(rec.get("volume")):
        problems.append(f"volume {rec.get('volume')} ≠ expected {expect_volume}")
    if expect_page:
        # Article-numbered journals (A&A, AJ, JATIS...) carry the number separately from the page range.
        cands = [_norm(str(rec.get(k))) for k in ("page", "article_number") if rec.get(k)]
        if cands and not any(_norm(str(expect_page)) in c.split() or c.startswith(_norm(str(expect_page)))
                             for c in cands):
            msg = f"page/article {rec.get('page')}/{rec.get('article_number')} ≠ expected {expect_page}"
            # Registry page metadata is unreliable for article-numbered journals; with a matching title
            # and volume this is a warning to eyeball, not a wrong DOI.
            (warnings if (sim or 0) >= 0.9 and not any("volume" in x for x in problems) else problems).append(msg)
    rec["problems"] = problems
    rec["warnings"] = warnings
    rec["ok"] = not problems
    return rec


async def _load_uat() -> list[dict]:
    global _uat_cache
    if _uat_cache is None:
        data = (await http_get(UAT_URL, timeout=120)).json()
        flat: list[dict] = []

        def walk(node: dict, path: list[str]):
            for ch in node.get("children") or []:
                p = path + [ch["name"]]
                flat.append(dict(name=ch["name"], id=int(ch["uri"].rsplit("/", 1)[-1]), uri=ch["uri"],
                                 alt=ch.get("altLabels") or [], path=" > ".join(p),
                                 deprecated=bool(ch.get("deprecated"))))
                walk(ch, p)

        walk(data, [])
        uniq = {}
        for c in flat:
            uniq.setdefault(c["id"], c)
        _uat_cache = list(uniq.values())
    return _uat_cache


async def uat_search(term: str, limit: int = 10) -> dict:
    """Find Unified Astronomy Thesaurus concepts (name, numeric ID, hierarchy path)."""
    uat = await _load_uat()
    t = _norm(term)
    scored = []
    for c in uat:
        names = [c["name"]] + list(c["alt"])
        best = max(difflib.SequenceMatcher(None, t, _norm(n)).ratio() + (0.5 if t in _norm(n) else 0)
                   for n in names)
        scored.append((best, c))
    scored.sort(key=lambda x: -x[0])
    hits = [dict(c, score=round(s, 3), exoplanet_branch="Exoplanet" in c["path"]) for s, c in scored[:limit]]
    return dict(term=term, hits=hits,
                note="Use \\uat{Name}{ID} style keywords; avoid exoplanet-branch concepts for EB papers.")


async def uat_verify(ids: list[int]) -> dict:
    uat = {c["id"]: c for c in await _load_uat()}
    return {str(i): (uat[i] if i in uat else {"error": "not a UAT concept ID"}) for i in ids}


def parse_bib(text: str) -> list[dict]:
    """Minimal brace-aware BibTeX reader: [{key, type, fields{name: value}}]."""
    entries = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        if m.group(1).lower() in ("comment", "string", "preamble"):
            continue
        i, depth, fields = m.end(), 1, {}
        while i < len(text) and depth > 0:
            fm = re.compile(r"\s*([A-Za-z_\-]+)\s*=\s*").match(text, i)
            if not fm:
                if text[i] == "}":
                    depth -= 1
                i += 1
                continue
            name, j = fm.group(1).lower(), fm.end()
            if text[j] in "{\"":
                close = "}" if text[j] == "{" else "\""
                d, k = 0, j
                while k < len(text):
                    c = text[k]
                    if c == "{":
                        d += 1
                    elif c == "}":
                        d -= 1
                    if (close == "}" and d == 0 and c == "}") or (close == "\"" and c == "\"" and k > j and d == 0):
                        break
                    k += 1
                val, i = text[j + 1:k], k + 1
            else:
                vm = re.compile(r"[^,}\s]+").match(text, j)
                val, i = (vm.group(0), vm.end()) if vm else ("", j)
            fields[name] = re.sub(r"\s+", " ", val.replace("{", "").replace("}", "")).strip()
            while i < len(text) and text[i] in " \t\r\n,":
                i += 1
            if i < len(text) and text[i] == "}":
                depth -= 1
                i += 1
        entries.append(dict(key=m.group(2), type=m.group(1).lower(), fields=fields))
    return entries
