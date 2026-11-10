"""Tests for rag/retriever.py (M3).

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import pytest

from rag.index_builder import build_index
from rag.retriever import IndexNotReadyError, Retriever, format_for_tool
from rag.schemas import Chunk, RetrievedChunk
from tests.conftest import requires_bge

TEXTS = [
    "Pregnant women should take daily oral iron and folic acid supplements to prevent anaemia.",
    "Severe headache and blurred vision in pregnancy can be signs of pre-eclampsia and need urgent care.",
    "Breastfeeding should be initiated within the first hour after birth.",
    "Gestational diabetes is diagnosed with a 75 g oral glucose tolerance test.",
]


def _chunks() -> list[Chunk]:
    return [Chunk(chunk_id=f"c{i}", text=t, source=f"doc{i}.pdf", title=f"Doc {i}",
                  organization="WHO", year="2020", category="antenatal_care", page=i + 1,
                  language="en", n_tokens=20) for i, t in enumerate(TEXTS)]


@pytest.fixture()
def fake_retriever(fake_embedder, chroma_client) -> Retriever:
    build_index(_chunks(), fake_embedder, chroma_client, "col_t", 400, 60)
    return Retriever(fake_embedder, chroma_client.get_collection("col_t"))


def test_similarity_in_unit_range_and_sorted(fake_retriever):
    hits = fake_retriever.search("iron and folic acid supplements", k=4, threshold=0.0)
    assert hits and all(0.0 <= h.similarity <= 1.0 for h in hits)
    assert [h.similarity for h in hits] == sorted((h.similarity for h in hits), reverse=True)
    assert hits[0].chunk_id == "c0"


def test_threshold_drops_low_scores(fake_retriever):
    hits = fake_retriever.search("iron and folic acid supplements", k=4, threshold=0.0)
    cut = (hits[0].similarity + hits[1].similarity) / 2  # between the 1st and 2nd score
    strict = fake_retriever.search("iron and folic acid supplements", k=4, threshold=cut)
    assert len(strict) == 1
    assert fake_retriever.search("quantum chromodynamics lattice", k=4, threshold=0.99) == []


def test_near_duplicates_are_dropped(fake_embedder, chroma_client):
    dup = _chunks() + [Chunk(chunk_id="dup", text=TEXTS[0] + " ", source="doc0.pdf", title="Doc 0",
                             organization="WHO", year="2020", category="antenatal_care", page=1,
                             language="en", n_tokens=20)]
    build_index(dup, fake_embedder, chroma_client, "col_d", 400, 60)
    r = Retriever(fake_embedder, chroma_client.get_collection("col_d"))
    hits = r.search("iron and folic acid supplements", k=4, threshold=0.0)
    assert sum(1 for h in hits if h.source == "doc0.pdf") == 1


def test_category_filter(fake_embedder, chroma_client):
    chunks = _chunks()
    chunks[2] = chunks[2].model_copy(update={"category": "lactation"})
    build_index(chunks, fake_embedder, chroma_client, "col_c", 400, 60)
    r = Retriever(fake_embedder, chroma_client.get_collection("col_c"))
    hits = r.search("iron folic acid", k=4, threshold=0.0, category="lactation")
    assert [h.chunk_id for h in hits] == ["c2"]


def test_empty_index_refused(fake_embedder, chroma_client):
    from rag.index_builder import create_collection

    col = create_collection(chroma_client, "col_empty", "fake-embedder", 400, 60)
    with pytest.raises(IndexNotReadyError):
        Retriever(fake_embedder, col)


def test_format_for_tool_numbers_chunks():
    c = RetrievedChunk(chunk_id="a", text="Some text", source="s.pdf", title="T", page=3,
                       category="x", similarity=0.8)
    out = format_for_tool([c, c])
    assert out.startswith("[1] Source: T (s.pdf, page 3") and "[2]" in out
    assert format_for_tool([]) == "No relevant guideline text was found."


@requires_bge
@pytest.mark.model
def test_arabic_query_finds_english_chunk(chroma_client):
    """Cross-lingual retrieval with the real bge-m3 model."""
    from rag.embeddings import Embedder

    emb = Embedder()
    build_index(_chunks(), emb, chroma_client, "col_x", 400, 60)
    r = Retriever(emb, chroma_client.get_collection("col_x"))
    hits = r.search("إمتى لازم أبدأ الرضاعة الطبيعية بعد الولادة؟", k=2, threshold=0.0)
    assert hits[0].chunk_id == "c2"
    hits = r.search("عندي صداع جامد وزغللة في عيني وأنا حامل", k=2, threshold=0.0)
    assert hits[0].chunk_id == "c1"
