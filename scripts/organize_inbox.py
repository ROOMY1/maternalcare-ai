"""Sort the downloaded guideline PDFs from data/raw/_inbox into data/raw/<category>/.

Purpose
    The user downloads every PDF listed in data/sources.csv into data/raw/_inbox with whatever
    name the browser gives it. This script recognises each file, renames it to the manifest
    name (<org>_<topic>_<year>.pdf), moves it into its category folder and checks that its
    text can be selected (i.e. it is not a scanned image).

Inputs
    data/sources.csv     the corpus manifest (file, category, ...)
    data/inbox_map.csv   helper only for this script: download-name keys and byte sizes
    data/raw/_inbox/*.pdf

Outputs
    data/raw/<category>/<file>.pdf and a printed report (matched, missing, unmatched, text check).

Usage
    python scripts/organize_inbox.py            # move + check
    python scripts/organize_inbox.py --dry-run  # only show what would happen
    python scripts/organize_inbox.py --check    # only re-check the files already sorted

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
from pathlib import Path

import pymupdf as fitz  # PyMuPDF (the old 'fitz' name is deprecated)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA = BASE_DIR / "data"
INBOX = DATA / "raw" / "_inbox"
MIN_CHARS = 50  # a page with fewer characters than this is probably a scanned image


def _norm(s: str) -> str:
    """Lower-case and drop everything except letters/digits, so 'NSAIDs DSC_Final' == 'nsaidsdscfinal'."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_manifest() -> dict[str, dict[str, str]]:
    with open(DATA / "sources.csv", encoding="utf-8") as f:
        rows = {r["file"]: r for r in csv.DictReader(f)}
    with open(DATA / "inbox_map.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows[r["file"]]["_keys"] = r["match_keys"]
            rows[r["file"]]["_size"] = r["size_bytes"]
    return rows


def match(path: Path, manifest: dict[str, dict[str, str]]) -> str | None:
    """Return the manifest file name for a downloaded PDF, by name key first, then by byte size."""
    if path.name in manifest:  # already has its final name (e.g. downloaded by the PowerShell loop)
        return path.name
    name = _norm(path.stem)
    for target, row in manifest.items():
        if any(_norm(k) and _norm(k) in name for k in row.get("_keys", "").split(";")):
            return target
    size = str(path.stat().st_size)
    for target, row in manifest.items():
        if row.get("_size") == size:
            return target
    return None


def text_check(path: Path) -> tuple[str, int, int, list[str]]:
    """Return (verdict, n_pages, n_low_text_pages, years_seen_on_first_page)."""
    with fitz.open(path) as doc:
        n = doc.page_count
        low = sum(1 for p in doc if len(p.get_text().strip()) < MIN_CHARS)
        first = doc[0].get_text() if n else ""
    ratio = low / n if n else 1.0
    verdict = "OK" if ratio <= 0.2 else ("PARTLY-SCANNED" if ratio < 0.6 else "SCANNED")
    years = sorted(set(re.findall(r"\b(?:19|20)\d{2}\b", first)))
    return verdict, n, low, years


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true", help="only check files already in place")
    args = ap.parse_args()

    manifest = load_manifest()
    unmatched: list[Path] = []
    if not args.check and INBOX.exists():
        for pdf in sorted(INBOX.glob("*.pdf")):
            if pdf.read_bytes()[:5] != b"%PDF-":
                print(f"[NOT A PDF] {pdf.name} (probably an HTML error page; download it again)")
                continue
            target = match(pdf, manifest)
            if target is None:
                unmatched.append(pdf)
                continue
            dest = DATA / "raw" / manifest[target]["category"] / target
            print(f"[MATCH] {pdf.name}  ->  {dest.relative_to(BASE_DIR)}")
            if not args.dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(pdf), dest)

    print("\n=== Text check (selectable text, not scanned) ===")
    missing = []
    for target, row in manifest.items():
        dest = DATA / "raw" / row["category"] / target
        if not dest.exists():
            missing.append(target)
            continue
        verdict, n, low, years = text_check(dest)
        extra = f"  years on page 1: {years}" if "TBC" in target else ""
        print(f"[{verdict:<14}] {target:<48} pages={n:<4} low-text pages={low}{extra}")

    if unmatched:
        print("\n=== Unmatched files in _inbox (rename them by hand, or add a key to data/inbox_map.csv) ===")
        for p in unmatched:
            print(f"  {p.name} ({p.stat().st_size} bytes)")
    if missing:
        print(f"\n=== Missing ({len(missing)}) — not downloaded yet ===")
        for m in missing:
            print(f"  {m}  <- {manifest[m]['url']}")
    print(f"\nSorted: {len(manifest) - len(missing)}/{len(manifest)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
