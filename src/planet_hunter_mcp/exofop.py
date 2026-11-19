"""ExoFOP-TESS bulk-upload ZIPs (port of Vetstar's frontend/src/ExofopBulkPanel.tsx).

Rules (ExoFOP-TESS Bulk File Upload Instructions,
https://exofop.ipac.caltech.edu/tess/ExoFOP-TESS_Bulk_File_Upload_Instructions.pdf, and help.php "Data Tags"):

- Archive ``xxYYYYMMDD-nnn.zip`` (xx = your initials, UT date, 3-digit counter), flat, holding a
  descriptor ``xxYYYYMMDD-nnn.txt`` with the SAME base name. A renamed ZIP fails ("no descriptor").
- Each file is named ``TIC<id><C>-xx<YYYYMMDD>.<info>[y].<ext>``, C = file-type code
  (F finding chart, I image, L light curve, P photometry, S spectrum, O other), y = a letter for duplicates.
- Descriptor lines have five pipe-delimited columns: file | data tag | group | proprietary months | description.
  No group, so the proprietary period is 0.
- Data tag: ExoFOP's short-hand number, or ``YYYYMMDD_username_description_n`` (<=100 chars), where
  username is your ExoFOP LOGIN (not your initials).

Vetstar lesson (2026-11-19): two ZIPs on the same UT day need different counters (001, 002).
"""
from __future__ import annotations

import re
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path

EXOFOP_CODES = {"F", "I", "L", "P", "S", "O"}
BULK_RULES_URL = "https://exofop.ipac.caltech.edu/tess/ExoFOP-TESS_Bulk_File_Upload_Instructions.pdf"


def ut_datestamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%d")


def descriptor_text(s: str) -> str:
    """Plain ASCII, no pipes (pipes delimit columns)."""
    s = re.sub(r"[–—]", "-", s).replace("R⊕", "R_Earth").replace("⊕", "Earth")
    s = re.sub(r"[|\r\n]+", " ", s)
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r"[^\x20-\x7E]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def filename_info(s: str) -> str:
    """Optional-info part of a filename: letters, digits, dots and dashes only."""
    s = re.sub(r"[^A-Za-z0-9.-]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return re.sub(r"^[-.]+|[-.]+$", "", s)


def check_data_tag(tag: str) -> str | None:
    """Return a problem to show, or None when the tag is valid."""
    t = tag.strip()
    if not t:
        return "Data tag is required (column 2 of the descriptor)."
    if re.fullmatch(r"\d+", t):
        return None  # short-hand tag number ExoFOP gave an existing tag
    if len(t) > 100:
        return "Data tag must be 100 characters or fewer."
    parts = t.split("_")
    if len(parts) != 4:
        return (f'Data tag needs 4 parts joined by "_": YYYYMMDD_username_description_number '
                f"(this one has {len(parts)}).")
    date, user, desc, num = parts
    try:
        datetime.strptime(date, "%Y%m%d")
        if not re.fullmatch(r"\d{8}", date):
            raise ValueError
    except ValueError:
        return "Data tag part 1 must be a real date, YYYYMMDD."
    if not user or re.search(r"\s", user):
        return "Data tag part 2 must be your ExoFOP login username."
    if not re.fullmatch(r"[A-Za-z0-9-]+", desc):
        return "Data tag part 3 (description) may use only letters, numbers and hyphens."
    if not re.fullmatch(r"\d{1,14}", num):
        return "Data tag part 4 must be a number from 0 to 99999999999999."
    return None


def make_data_tag(username: str, description: str, n: int | str = 1, now: datetime | None = None) -> str:
    desc = unicodedata.normalize("NFKD", description)
    desc = "".join(c for c in desc if not unicodedata.combining(c))
    desc = re.sub(r"[^A-Za-z0-9-]+", "-", desc)
    desc = re.sub(r"-+", "-", desc).strip("-")
    return f"{ut_datestamp(now)}_{username.strip()}_{desc}_{str(n).strip() or '1'}"


def file_type_code(path: str | Path) -> str:
    """Pick the ExoFOP file-type code for a file when the caller did not give one."""
    p = Path(path)
    name, suffix = p.stem.lower(), p.suffix.lower()
    # Split on anything that is not a letter or digit, so "lc" matches "tic1_lc" but not "calc".
    words = set(re.split(r"[^a-z0-9]+", name))
    if "lightcurve" in words or "lc" in words or "fold" in words or "light_curve" in name or "odd_even" in name:
        return "L"
    elif suffix in (".fits", ".fit", ".fz"):
        return "I"  # pixel data: TESScut cutouts, target pixel files
    elif suffix in (".txt", ".csv"):
        return "P"  # photometry tables
    else:
        return "O"


def next_counter(out_dir: Path, initials: str, now: datetime | None = None) -> str:
    """First counter not already used today (UT) by a ZIP in out_dir."""
    day = ut_datestamp(now)
    used = {int(m.group(1)) for p in out_dir.glob(f"{initials}{day}-*.zip")
            if (m := re.fullmatch(rf"{re.escape(initials)}{day}-(\d{{3}})\.zip", p.name))}
    n = 1
    while n in used:
        n += 1
    if n > 999:
        raise ValueError("All 999 ExoFOP archive counters for today are used.")
    return f"{n:03d}"


def build_archive(*, tic: int, initials: str, tag: str, files: list[dict], out_dir: Path,
                  sector: int | None = None, counter: str | None = None, author: str | None = None,
                  source: str = "Vetstar", now: datetime | None = None) -> dict:
    """Write one ExoFOP-TESS bulk-upload ZIP.

    files: [{"path", "label", "code"?, "key"?}]. key defaults to the file stem.
    Returns {zip, descriptor, entries, counter}."""
    ini = initials.strip().lower()
    if not re.fullmatch(r"[a-z]{2,8}", ini):
        raise ValueError("Initials must be 2-8 letters (ExoFOP archive and file names start with them).")
    problem = check_data_tag(tag)
    if problem:
        raise ValueError(problem)
    out_dir.mkdir(parents=True, exist_ok=True)
    day = ut_datestamp(now)
    cnt = counter or next_counter(out_dir, ini, now)
    if not re.fullmatch(r"\d{3}", cnt):
        raise ValueError("Counter must be 3 digits, e.g. 001.")
    base = f"{ini}{day}-{cnt}"
    zpath = out_dir / f"{base}.zip"
    if zpath.exists():
        raise FileExistsError(f"{zpath.name} already exists; pass another counter (ExoFOP needs unique names).")
    credit = ""
    if author and author.strip():
        credit = f", by {author.strip()} ({ini})"

    seen: set[str] = set()
    planned = []
    for f in files:
        p = Path(f["path"]).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"{p} does not exist.")
        code = (f.get("code") or file_type_code(p)).upper()
        if code not in EXOFOP_CODES:
            raise ValueError(f'Unknown ExoFOP file-type code "{code}" for {p.name}.')
        ext = p.suffix.lstrip(".").lower() or "dat"
        key = filename_info((f.get("key") or p.stem).replace("_", "-")) or "file"
        stem = f"TIC{tic}{code}-{ini}{day}"
        name = f"{stem}.{key}.{ext}"
        if len(name) > 100:
            key = key[:max(100 - len(f"{stem}..{ext}"), 1)]
            name = f"{stem}.{key}.{ext}"
        if name in seen:
            root, suffix = name[: -(len(ext) + 1)], "a"
            while f"{root}{suffix}.{ext}" in seen:
                suffix = chr(ord(suffix) + 1)
            name = f"{root}{suffix}.{ext}"
        seen.add(name)
        planned.append((p, name, f.get("label") or p.stem))
    if not planned:
        raise ValueError("No files to bundle.")

    sec = f" (Sector {sector})" if sector else ""
    descriptor = "".join(
        f"{name}|{descriptor_text(tag)}||0|{descriptor_text(f'{source} - {label}{sec}{credit}')}\n"
        for _, name, label in planned)
    with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{base}.txt", descriptor)
        for p, name, _ in planned:
            z.write(p, arcname=name)
    return dict(zip=str(zpath), descriptor=descriptor, counter=cnt,
                entries=[f"{base}.txt", *[n for _, n, _ in planned]],
                bytes=zpath.stat().st_size,
                upload_steps=[
                    "Log in to ExoFOP-TESS and open 'Upload Files' -> 'Bulk upload'.",
                    f"Upload {zpath.name} exactly as named (do not rename: the descriptor must match).",
                    "Check the TIC page afterwards; files appear under the data tag.",
                ], rules=BULK_RULES_URL)
