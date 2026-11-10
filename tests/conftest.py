"""Shared test fixtures: tiny generated PDFs, a fake embedder and an in-temp Chroma client.

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import csv
import hashlib
import math
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.ingestion import MANIFEST_COLUMNS  # noqa: E402


def make_pdf(path: Path, pages: list[str]) -> Path:
    """Write a simple text PDF (one string per page) with PyMuPDF."""
    import pymupdf as fitz

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(50, 50, 550, 800), text, fontsize=10)
    doc.save(path)
    doc.close()
    return path


def write_manifest(path: Path, rows: list[dict[str, str]]) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "x") for c in MANIFEST_COLUMNS})
    return path


BODY = (
    "Pre-eclampsia is a pregnancy disorder with high blood pressure and protein in the urine. "
    "Women with severe headache, visual disturbance or upper abdominal pain should seek care urgently. "
)


@pytest.fixture()
def corpus(tmp_path: Path) -> dict[str, Path]:
    """A two-document corpus with a header/footer, a TOC page, a reference page and a blank page."""
    raw = tmp_path / "raw"
    pages = ["Contents\nIntroduction ........ 1\nMethods ........ 2\nResults ........ 3\nAnnex ........ 4"]
    for i in range(1, 6):
        pages.append(f"WHO Guideline Header\n{BODY * 6}\nPage {i}")
    pages.append("References\nSmith J et al. (2015) Lancet 385;12(3):100. doi:10.1/x\n"
                 "Doe A et al. (2018) BMJ 2018;360:k1. https://doi.org/10.1/y")
    pages.append("")  # blank -> scanned warning
    make_pdf(raw / "hypertension_preeclampsia" / "WHO_test-pe_2011.pdf", pages)
    make_pdf(raw / "lactation" / "WHO_test-bf_2017.pdf",
             ["Breastfeeding should start within the first hour after birth. " * 8])
    manifest = write_manifest(tmp_path / "sources.csv", [
        {"file": "WHO_test-pe_2011.pdf", "title": "PE test", "organization": "WHO", "year": "2011",
         "url": "https://example.org", "category": "hypertension_preeclampsia", "language": "en",
         "license": "test"},
        {"file": "WHO_test-bf_2017.pdf", "title": "BF test", "organization": "WHO", "year": "2017",
         "url": "https://example.org", "category": "lactation", "language": "en", "license": "test"},
    ])
    return {"raw": raw, "manifest": manifest, "out": tmp_path / "processed" / "chunks.json"}


class FakeEmbedder:
    """Deterministic bag-of-words embedder (no model download), 64 dimensions, normalized."""

    model_name = "fake-embedder"
    dim = 64

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for w in re.findall(r"\w+", text.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


@pytest.fixture()
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture()
def chroma_client(tmp_path: Path):
    from rag.index_builder import get_client

    return get_client(tmp_path / "vdb")


def bge_available() -> bool:
    """True when bge-m3 is already in the local Hugging Face cache (no download in tests)."""
    try:
        from huggingface_hub import try_to_load_from_cache

        return isinstance(try_to_load_from_cache("BAAI/bge-m3", "tokenizer_config.json"), str)
    except Exception:  # noqa: BLE001
        return False


requires_bge = pytest.mark.skipif(not bge_available(), reason="bge-m3 not downloaded yet")
