"""Tests for rag/ingestion.py (M1).

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import json

import pytest

from rag.ingestion import (ManifestError, check_manifest, clean_text, discover_pdfs, ingest,
                           load_manifest, load_pages, make_chunk_id, page_kind,
                           remove_repeated_lines)
from tests.conftest import requires_bge, write_manifest


def test_pages_are_one_based(corpus):
    pdf = corpus["raw"] / "lactation" / "WHO_test-bf_2017.pdf"
    pages = load_pages(pdf)
    assert pages[0][0] == 1


def test_clean_text_fixes_hyphenation_and_spaces():
    assert clean_text("pre-\neclampsia   is  \n\n\n\n common") == "preeclampsia is\n\ncommon"


def test_arabic_extraction_artifacts_fixed():
    from rag.ingestion import fix_arabic_extraction

    assert fix_arabic_extraction("واألساس االستفادة اإلسهال") == "والأساس الاستفادة الإسهال"
    assert fix_arabic_extraction("سـاعة الحمل") == "ساعة الحمل"  # tatweel removed, article kept


def test_repeated_header_footer_removed():
    topics = ["anaemia", "iron", "calcium", "blood pressure", "glucose", "breastfeeding"]
    pages = ["Header Line\n" + "\n".join(f"Paragraph {j} about {topics[i]} and line {i * j}."
                                         for j in range(8)) + f"\nPage {i}" for i in range(6)]
    out = remove_repeated_lines(pages)
    assert all("Header Line" not in p and "Page" not in p for p in out)
    assert all("Paragraph 3 about" in p for p in out)


def test_page_kind_detects_toc_and_references():
    assert page_kind("Contents\nIntro ..... 1\nMethods ..... 4\nResults ..... 9") == "toc"
    refs = "\n".join(f"Author{i} B et al. (2019) Journal 12(3):45. doi:10.1/{i}" for i in range(6))
    assert page_kind("References\n" + refs) == "references"
    assert page_kind("Women should receive iron and folic acid during pregnancy.") == "content"


def test_stable_chunk_ids():
    assert make_chunk_id("a.pdf", 3, 0) == make_chunk_id("a.pdf", 3, 0)
    assert make_chunk_id("a.pdf", 3, 0) != make_chunk_id("a.pdf", 3, 1)


def test_manifest_requires_row_for_every_pdf(corpus, tmp_path):
    manifest = load_manifest(corpus["manifest"])
    pdfs = discover_pdfs(corpus["raw"])
    check_manifest(pdfs, manifest)  # complete -> no error
    manifest.pop("WHO_test-bf_2017.pdf")
    with pytest.raises(ManifestError, match="without a row"):
        check_manifest(pdfs, manifest)


def test_manifest_row_without_file_fails(corpus):
    manifest = load_manifest(corpus["manifest"])
    manifest["ghost.pdf"] = dict(manifest["WHO_test-bf_2017.pdf"], file="ghost.pdf")
    with pytest.raises(ManifestError, match="without a PDF"):
        check_manifest(discover_pdfs(corpus["raw"]), manifest)


def test_manifest_empty_cell_fails(tmp_path):
    path = write_manifest(tmp_path / "m.csv", [{"file": "a.pdf", "title": ""}])
    with pytest.raises(ManifestError, match="empty"):
        load_manifest(path)


def test_inbox_is_ignored(corpus):
    (corpus["raw"] / "_inbox").mkdir()
    (corpus["raw"] / "_inbox" / "stray.pdf").write_bytes(b"%PDF-1.4")
    names = [p.name for p in discover_pdfs(corpus["raw"])]
    assert "stray.pdf" not in names and len(names) == 2


@requires_bge
@pytest.mark.model
def test_ingest_end_to_end(corpus):
    from rag.ingestion import load_tokenizer

    tok = load_tokenizer("BAAI/bge-m3")
    summary = ingest(corpus["raw"], corpus["manifest"], corpus["out"], chunk_size=120,
                     chunk_overlap=20, tokenizer=tok)
    data = json.loads(corpus["out"].read_text(encoding="utf-8"))
    chunks = data["chunks"]
    assert summary.documents == 2 and summary.chunks == len(chunks) > 2
    required = {"chunk_id", "source", "title", "organization", "year", "category", "page",
                "language", "n_tokens", "text"}
    for c in chunks:
        assert required <= c.keys() and all(c[k] not in ("", None) for k in required)
        assert c["n_tokens"] <= 120  # chunk never longer than chunk_size
        assert c["page"] >= 1
        assert "WHO Guideline Header" not in c["text"]
    assert any("toc" in d for d in summary.dropped_pages)
    assert any("references" in d for d in summary.dropped_pages)
    assert summary.scanned_pages  # the blank page is reported
    assert len({c["chunk_id"] for c in chunks}) == len(chunks)  # unique IDs
    # running again gives the same IDs (stable)
    ingest(corpus["raw"], corpus["manifest"], corpus["out"], chunk_size=120, chunk_overlap=20,
           tokenizer=tok)
    again = json.loads(corpus["out"].read_text(encoding="utf-8"))["chunks"]
    assert [c["chunk_id"] for c in again] == [c["chunk_id"] for c in chunks]


@requires_bge
@pytest.mark.model
def test_chunk_size_above_model_limit_is_rejected():
    from rag.ingestion import load_tokenizer, make_splitter

    with pytest.raises(AssertionError):
        make_splitter(load_tokenizer("BAAI/bge-m3"), 9000, 60, "BAAI/bge-m3")
