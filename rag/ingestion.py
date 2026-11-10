"""Step 1-2 of the pipeline: load the guideline PDFs, clean them and cut them into chunks.

Purpose
    Turn every PDF listed in data/sources.csv into token-sized chunks with full metadata and
    write them to data/processed/processed_chunks.json (the input of index_builder.py).

Pipeline inside this file
    manifest check -> load pages (PyMuPDF, 1-based page numbers) -> warn on scanned pages
    -> clean text -> remove repeated headers/footers -> drop TOC / reference pages
    -> split each page with a token splitter built on the embedding model's tokenizer
    -> metadata + stable chunk_id -> JSON + summary.

Chunks never cross a page boundary, so every chunk has exactly one page number to cite.

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from config import EMBED_MODEL_MAX_TOKENS, PROCESSED_CHUNKS, RAW_DIR, SOURCES_CSV, get_settings
from rag.schemas import Chunk
from safety.arabic_text import detect_language

logger = logging.getLogger(__name__)

MANIFEST_COLUMNS = ["file", "title", "organization", "year", "url", "category", "language", "license"]
MIN_PAGE_CHARS = 50  # fewer characters than this -> probably a scanned (image-only) page
INBOX_DIRNAME = "_inbox"


class ManifestError(ValueError):
    """Raised when data/sources.csv and data/raw/ do not match."""


class Tokenizer(Protocol):
    """The part of a Hugging Face tokenizer that we use."""

    def tokenize(self, text: str) -> list[str]: ...


@dataclass
class IngestSummary:
    """Numbers printed after ingestion (and used in the report)."""

    documents: int = 0
    pages_total: int = 0
    pages_kept: int = 0
    chunks: int = 0
    dropped_pages: list[str] = field(default_factory=list)
    scanned_pages: list[str] = field(default_factory=list)
    token_histogram: dict[str, int] = field(default_factory=dict)
    per_document: dict[str, int] = field(default_factory=dict)

    def as_text(self) -> str:
        lines = [
            f"Documents: {self.documents}",
            f"Pages: {self.pages_total} (kept {self.pages_kept})",
            f"Chunks: {self.chunks}",
            "Tokens per chunk: " + ", ".join(f"{k}: {v}" for k, v in self.token_histogram.items()),
            f"Dropped pages (TOC/references/empty): {len(self.dropped_pages)}",
            f"Probably scanned pages (< {MIN_PAGE_CHARS} chars): {len(self.scanned_pages)}",
        ]
        for w in self.scanned_pages[:20]:
            lines.append(f"  WARNING scanned? {w}")
        return "\n".join(lines)


# ----------------------------------------------------------------------------- manifest
def load_manifest(path: Path = SOURCES_CSV) -> dict[str, dict[str, str]]:
    """Read sources.csv into {file name: row}. Fails loudly on missing columns or empty cells."""
    if not path.exists():
        raise ManifestError(f"Manifest not found: {path}. Create data/sources.csv (see README).")
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in MANIFEST_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ManifestError(f"sources.csv is missing columns: {missing}")
        rows: dict[str, dict[str, str]] = {}
        for i, row in enumerate(reader, start=2):
            empty = [c for c in MANIFEST_COLUMNS if not (row.get(c) or "").strip()]
            if empty:
                raise ManifestError(f"sources.csv line {i} ({row.get('file')}): empty {empty}")
            if row["language"] not in ("ar", "en"):
                raise ManifestError(f"sources.csv line {i}: language must be 'ar' or 'en'")
            rows[row["file"].strip()] = {k: v.strip() for k, v in row.items() if k}
    return rows


def discover_pdfs(raw_dir: Path = RAW_DIR) -> list[Path]:
    """All PDFs under data/raw/<category>/, ignoring the download inbox."""
    return sorted(
        p for p in raw_dir.rglob("*.pdf") if INBOX_DIRNAME not in p.relative_to(raw_dir).parts
    )


def check_manifest(pdfs: list[Path], manifest: dict[str, dict[str, str]]) -> None:
    """Every PDF needs a manifest row and every row needs a PDF in the right category folder."""
    names = {p.name: p for p in pdfs}
    no_row = sorted(set(names) - set(manifest))
    no_file = sorted(set(manifest) - set(names))
    errors = []
    if no_row:
        errors.append(f"PDFs without a row in sources.csv: {no_row}")
    if no_file:
        errors.append(f"Rows in sources.csv without a PDF in data/raw/<category>/: {no_file}")
    for name, path in names.items():
        if name in manifest and path.parent.name != manifest[name]["category"]:
            errors.append(f"{name} is in folder '{path.parent.name}' but its category is "
                          f"'{manifest[name]['category']}'")
    if errors:
        raise ManifestError("\n".join(errors) + "\nFix: python scripts/organize_inbox.py, or edit "
                            "data/sources.csv")


# ----------------------------------------------------------------------------- loading
def load_pages(path: Path) -> list[tuple[int, str]]:
    """Return [(page_number_1_based, text)] for one PDF.

    PyMuPDFLoader gives a 0-based "page" in its metadata, so we add 1 (the "page + 1" fix),
    which matches the number shown by PDF viewers. Falls back to PyPDFLoader on error.
    """
    try:
        from langchain_community.document_loaders import PyMuPDFLoader

        docs = PyMuPDFLoader(str(path)).load()
    except Exception as exc:  # noqa: BLE001 - any PyMuPDF failure -> try the other loader
        logger.warning("PyMuPDF failed on %s (%s); falling back to PyPDFLoader", path.name, exc)
        from langchain_community.document_loaders import PyPDFLoader

        docs = PyPDFLoader(str(path)).load()
    return [(int(d.metadata.get("page", i)) + 1, d.page_content or "") for i, d in enumerate(docs)]


# ----------------------------------------------------------------------------- cleaning
_SWAPPED_LAM_ALEF = re.compile("ا([اأإآ])ل")


def fix_arabic_extraction(text: str) -> str:
    """Repair two PDF-extraction artifacts seen in the WHO Arabic PDFs.

    1. Tatweel (ـ, U+0640) used to stretch words for justification: "سـاعة" -> "ساعة".
    2. The lam-alef ligature (لا / لأ / لإ / لآ) after the article "ال" is extracted in the wrong
       order, e.g. "والأساس" comes out as "واألساس" and "الاستفادة" as "االستفادة".
       The sequence alef + alef + lam never occurs in correct Arabic, so it is safe to swap back.
    (Mid-word ligatures such as "خلال" -> "خالل" are ambiguous and are left as they are.)
    """
    return _SWAPPED_LAM_ALEF.sub(r"ال\1", text.replace("ـ", ""))


def clean_text(text: str) -> str:
    """Fix hyphenated line breaks and Arabic extraction artifacts, collapse spaces, keep paragraphs."""
    text = fix_arabic_extraction(text)
    text = text.replace("\r", "\n").replace("­", "")  # soft hyphen
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # "pre-\neclampsia" -> "preeclampsia"
    text = re.sub(r"[ \t ]+", " ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _line_key(line: str) -> str:
    """Header/footer lines differ only by page number, so compare them without digits."""
    return re.sub(r"\d+", "#", line.strip().lower())


def remove_repeated_lines(pages: list[str], edge: int = 3, min_share: float = 0.5) -> list[str]:
    """Remove header/footer lines that repeat on at least `min_share` of a document's pages.

    Only the first and last `edge` lines of each page are considered, so repeated sentences in
    the body are never removed. Documents with fewer than 4 pages are left unchanged.
    """
    if len(pages) < 4:
        return pages
    counts: Counter[str] = Counter()
    for text in pages:
        lines = [ln for ln in text.split("\n") if ln.strip()]
        counts.update({_line_key(ln) for ln in lines[:edge] + lines[-edge:]})
    repeated = {k for k, c in counts.items() if c >= min_share * len(pages) and k.strip("# ")}
    out = []
    for text in pages:
        lines = text.split("\n")
        non_empty = [i for i, ln in enumerate(lines) if ln.strip()]
        edge_idx = set(non_empty[:edge] + non_empty[-edge:])
        out.append("\n".join(ln for i, ln in enumerate(lines)
                             if not (i in edge_idx and _line_key(ln) in repeated)))
    return out


_TOC_WORDS = re.compile(r"^\s*(table of contents|contents|المحتويات|الفهرس|فهرس المحتويات)\s*$",
                        re.IGNORECASE | re.MULTILINE)
_REF_WORDS = re.compile(r"^\s*(references|bibliography|المراجع|المصادر)\s*$",
                        re.IGNORECASE | re.MULTILINE)
_REF_LINE = re.compile(r"et al\.?|doi[:.]|https?://|\(\d{4}\)|;\s*\d+\(\d+\)|\b\d{4};\s*\d+", re.I)


def page_kind(text: str) -> str:
    """Classify a cleaned page as "toc", "references", "empty" or "content"."""
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        return "empty"
    head = "\n".join(lines[:6])
    ends_with_number = sum(1 for ln in lines if re.search(r"(\.{2,}|\s)\d{1,3}$", ln)) / len(lines)
    ref_like = sum(1 for ln in lines if _REF_LINE.search(ln)) / len(lines)
    if _TOC_WORDS.search(head) and ends_with_number >= 0.3:
        return "toc"
    if ref_like >= 0.5 or (_REF_WORDS.search(head) and ref_like >= 0.25):
        return "references"
    return "content"


# ----------------------------------------------------------------------------- chunking
def load_tokenizer(model_name: str | None = None) -> Any:
    """Load the embedding model's own tokenizer, so chunk sizes are measured in its tokens."""
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(model_name or get_settings().embed_model)


def make_splitter(tokenizer: Any, chunk_size: int, chunk_overlap: int, model_name: str) -> Any:
    """Token-based RecursiveCharacterTextSplitter built on the embedding tokenizer.

    Asserts chunk_size <= the model's max input length (otherwise the embedder would silently
    truncate the end of every chunk, which was the bug in the original plan).
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    max_len = EMBED_MODEL_MAX_TOKENS.get(model_name, getattr(tokenizer, "model_max_length", 512))
    assert chunk_size <= max_len, f"chunk_size {chunk_size} > {model_name} max length {max_len}"
    assert chunk_overlap < chunk_size, "chunk_overlap must be smaller than chunk_size"
    return RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
        tokenizer,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", "۔ ", "؟ ", "? ", "; ", "، ", ", ", " ", ""],
    )


def split_to_limit(text: str, splitter: Any, tokenizer: Any, limit: int) -> list[str]:
    """Split `text` and guarantee every piece has at most `limit` tokens.

    The splitter estimates a merged chunk's length by adding the token counts of its parts, but
    the tokenizer can produce a few more tokens for the joined text (sub-words merge differently
    across the joins). Any piece that ends up too long is split again with a 20% smaller target.
    """
    out: list[str] = []
    for piece in splitter.split_text(text):
        if len(tokenizer.tokenize(piece)) <= limit:
            out.append(piece)
            continue
        smaller = type(splitter).from_huggingface_tokenizer(
            tokenizer, chunk_size=int(splitter._chunk_size * 0.8),
            chunk_overlap=splitter._chunk_overlap, separators=splitter._separators)
        smaller._chunk_size = int(splitter._chunk_size * 0.8)
        out.extend(split_to_limit(piece, smaller, tokenizer, limit))
    return out


def make_chunk_id(source: str, page: int, index: int) -> str:
    """Stable ID: the same file, page and position always give the same ID (idempotent indexing)."""
    return hashlib.sha1(f"{source}|{page}|{index}".encode()).hexdigest()[:16]


def _histogram(values: list[int], bins: tuple[int, ...] = (100, 200, 300, 400, 500)) -> dict[str, int]:
    """Count values in bins (lo, hi], e.g. "301-400" includes a chunk of exactly 400 tokens."""
    hist: dict[str, int] = {}
    lo = 0
    for hi in bins:
        hist[f"{lo + 1 if lo else 0}-{hi}"] = sum(1 for v in values if lo < v <= hi or (lo == 0 and v == 0))
        lo = hi
    hist[f">{lo}"] = sum(1 for v in values if v > lo)
    return hist


def chunk_document(path: Path, meta: dict[str, str], splitter: Any, tokenizer: Any,
                   summary: IngestSummary) -> list[Chunk]:
    """Load, clean and chunk one PDF. Updates `summary` with warnings and drops."""
    raw_pages = load_pages(path)
    summary.pages_total += len(raw_pages)
    for page_no, text in raw_pages:
        if len(text.strip()) < MIN_PAGE_CHARS:
            summary.scanned_pages.append(f"{path.name} p.{page_no}")
    cleaned = remove_repeated_lines([clean_text(t) for _, t in raw_pages])

    chunks: list[Chunk] = []
    for (page_no, _), text in zip(raw_pages, cleaned):
        kind = page_kind(text)
        if kind != "content":
            summary.dropped_pages.append(f"{path.name} p.{page_no} ({kind})")
            logger.debug("drop %s p.%d (%s)", path.name, page_no, kind)
            continue
        summary.pages_kept += 1
        for idx, piece in enumerate(split_to_limit(text, splitter, tokenizer, splitter._chunk_size)):
            piece = piece.strip()
            if len(piece) < 20:  # crumbs such as a lone heading or page number
                continue
            chunks.append(Chunk(
                chunk_id=make_chunk_id(path.name, page_no, idx),
                text=piece,  # raw text: no model prefixes are stored
                source=path.name,
                title=meta["title"],
                organization=meta["organization"],
                year=meta["year"],
                category=meta["category"],
                page=page_no,
                language=detect_language(piece) if meta["language"] == "ar" else "en",
                n_tokens=len(tokenizer.tokenize(piece)),
            ))
    return chunks


def ingest(raw_dir: Path = RAW_DIR, manifest_path: Path = SOURCES_CSV,
           out_path: Path = PROCESSED_CHUNKS, chunk_size: int | None = None,
           chunk_overlap: int | None = None, tokenizer: Any = None) -> IngestSummary:
    """Run the whole ingestion step and write processed_chunks.json.

    Args:
        raw_dir: folder with data/raw/<category>/*.pdf
        manifest_path: sources.csv
        out_path: where the chunks JSON is written
        chunk_size / chunk_overlap: in tokens; default from config (.env)
        tokenizer: inject a tokenizer (tests); default = the embedding model's tokenizer
    """
    s = get_settings()
    chunk_size = chunk_size or s.chunk_size_tokens
    chunk_overlap = s.chunk_overlap_tokens if chunk_overlap is None else chunk_overlap
    manifest = load_manifest(manifest_path)
    pdfs = discover_pdfs(raw_dir)
    check_manifest(pdfs, manifest)

    tokenizer = tokenizer or load_tokenizer(s.embed_model)
    splitter = make_splitter(tokenizer, chunk_size, chunk_overlap, s.embed_model)
    summary = IngestSummary(documents=len(pdfs))
    all_chunks: list[Chunk] = []
    for pdf in pdfs:
        doc_chunks = chunk_document(pdf, manifest[pdf.name], splitter, tokenizer, summary)
        summary.per_document[pdf.name] = len(doc_chunks)
        if not doc_chunks:
            logger.warning("%s produced no chunks (scanned or empty?)", pdf.name)
        all_chunks.extend(doc_chunks)

    summary.chunks = len(all_chunks)
    summary.token_histogram = _histogram([c.n_tokens for c in all_chunks])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "embed_model": s.embed_model,
        "chunk_size_tokens": chunk_size,
        "chunk_overlap_tokens": chunk_overlap,
        "chunks": [c.model_dump() for c in all_chunks],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def read_chunks(path: Path = PROCESSED_CHUNKS) -> tuple[dict[str, Any], list[Chunk]]:
    """Read processed_chunks.json -> (header settings, chunks)."""
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run: python cli.py ingest")
    data = json.loads(path.read_text(encoding="utf-8"))
    chunks = [Chunk(**c) for c in data.pop("chunks")]
    return data, chunks
