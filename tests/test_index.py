"""Tests for rag/index_builder.py (M2) on a tiny fixture collection with a fake embedder.

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import pytest

from rag.index_builder import build_index, collection_space
from rag.schemas import Chunk


def _chunks(n: int = 5) -> list[Chunk]:
    topics = ["iron folic acid anaemia", "pre-eclampsia blood pressure", "breastfeeding first hour",
              "gestational diabetes glucose test", "postpartum haemorrhage oxytocin"]
    return [Chunk(chunk_id=f"id{i}", text=f"Guideline text about {topics[i % 5]} number {i}.",
                  source="x.pdf", title="X", organization="WHO", year="2020",
                  category="antenatal_care", page=i + 1, language="en", n_tokens=10)
            for i in range(n)]


def test_upsert_is_idempotent(fake_embedder, chroma_client):
    chunks = _chunks()
    s1 = build_index(chunks, fake_embedder, chroma_client, "col_t", 400, 60)
    s2 = build_index(chunks, fake_embedder, chroma_client, "col_t", 400, 60)
    assert s1.collection_count == s2.collection_count == len(chunks)


def test_stale_chunks_are_removed(fake_embedder, chroma_client):
    build_index(_chunks(5), fake_embedder, chroma_client, "col_t", 400, 60)
    stats = build_index(_chunks(3), fake_embedder, chroma_client, "col_t", 400, 60)
    assert stats.collection_count == 3


def test_collection_uses_cosine(fake_embedder, chroma_client):
    build_index(_chunks(), fake_embedder, chroma_client, "col_t", 400, 60)
    col = chroma_client.get_collection("col_t")
    assert collection_space(col) == "cosine"
    assert col.metadata["embed_model"] == "fake-embedder"


def test_rebuild_with_other_model_required(fake_embedder, chroma_client):
    build_index(_chunks(), fake_embedder, chroma_client, "col_t", 400, 60)

    class Other(type(fake_embedder)):
        model_name = "another-model"

    with pytest.raises(RuntimeError, match="rebuild"):
        build_index(_chunks(), Other(), chroma_client, "col_t", 400, 60)
    stats = build_index(_chunks(), Other(), chroma_client, "col_t", 400, 60, rebuild=True)
    assert stats.collection_count == 5


def test_retriever_refuses_model_mismatch(fake_embedder, chroma_client):
    from rag.retriever import IndexMismatchError, Retriever

    build_index(_chunks(), fake_embedder, chroma_client, "col_t", 400, 60)

    class Other(type(fake_embedder)):
        model_name = "another-model"

    with pytest.raises(IndexMismatchError):
        Retriever(Other(), chroma_client.get_collection("col_t"))
