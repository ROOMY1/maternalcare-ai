"""Step 5 of the pipeline: find the guideline chunks most similar to a question.

Purpose
    Embed the question with the same model used for the index, search Chroma by cosine
    distance, convert distance to similarity (1 - distance), drop weak and near-duplicate hits
    and return the top-k.

Public interface (stable, becomes the Phase 2 `search_maternal_guidelines` tool body):
    retrieve(query, k=TOP_K, threshold=SIMILARITY_THRESHOLD, category=None) -> list[RetrievedChunk]
    format_for_tool(chunks) -> str

Arabic questions go straight to bge-m3, which maps Arabic and English text with the same
meaning to nearby vectors (cross-lingual retrieval). There is no translation step in Phase 1.

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import re
import time
from functools import lru_cache
from typing import Any, Optional

from config import get_settings
from rag.index_builder import EmbedderLike, collection_space, get_client
from rag.schemas import RetrievedChunk


class IndexNotReadyError(RuntimeError):
    """The Chroma collection is missing or empty."""


class IndexMismatchError(RuntimeError):
    """The index was built with a different embedding model than the one configured."""


_WORD = re.compile(r"\w+", re.UNICODE)


def _jaccard(a: str, b: str) -> float:
    wa, wb = set(_WORD.findall(a.lower())), set(_WORD.findall(b.lower()))
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


class Retriever:
    """Holds the embedder and the collection; inject fakes in tests."""

    def __init__(self, embedder: EmbedderLike, collection: Any, dedup_jaccard: float = 0.8) -> None:
        built_with = (collection.metadata or {}).get("embed_model")
        if built_with and built_with != embedder.model_name:
            raise IndexMismatchError(
                f"The index was built with '{built_with}' but EMBED_MODEL is "
                f"'{embedder.model_name}'. Rebuild it: python cli.py index --rebuild --yes")
        if collection_space(collection) != "cosine":
            raise IndexMismatchError("The collection does not use cosine distance. "
                                     "Rebuild it: python cli.py index --rebuild --yes")
        if collection.count() == 0:
            raise IndexNotReadyError("The index is empty. Run: python cli.py ingest, then "
                                     "python cli.py index")
        self.embedder = embedder
        self.collection = collection
        self.dedup_jaccard = dedup_jaccard
        self.last_timing: dict[str, float] = {"embed": 0.0, "search": 0.0}

    def search(self, query: str, k: int, threshold: float,
               category: Optional[str] = None) -> list[RetrievedChunk]:
        """Top-k chunks with similarity >= threshold, best first, near-duplicates removed."""
        t0 = time.perf_counter()
        qvec = self.embedder.embed_query(query)
        t1 = time.perf_counter()
        res = self.collection.query(
            query_embeddings=[qvec],
            n_results=min(k * 3, self.collection.count()),  # extra room for de-duplication
            where={"category": category} if category else None,
            include=["documents", "metadatas", "distances"],
        )
        t2 = time.perf_counter()
        self.last_timing = {"embed": (t1 - t0) * 1000, "search": (t2 - t1) * 1000}

        hits: list[RetrievedChunk] = []
        for cid, doc, meta, dist in zip(res["ids"][0], res["documents"][0],
                                        res["metadatas"][0], res["distances"][0]):
            # Cosine distance is in [0, 2]; similarity = 1 - distance, clipped to [0, 1].
            sim = max(0.0, min(1.0, 1.0 - float(dist)))
            if sim < threshold:
                continue
            if any(_jaccard(doc, h.text) >= self.dedup_jaccard for h in hits):
                continue  # overlapping neighbour chunk: adds nothing new
            hits.append(RetrievedChunk(
                chunk_id=cid, text=doc, source=meta["source"], title=meta["title"],
                page=int(meta["page"]), category=meta["category"],
                organization=meta.get("organization"), year=str(meta.get("year", "")),
                similarity=round(sim, 4)))
        hits.sort(key=lambda h: h.similarity, reverse=True)
        return hits[:k]

    def search_multi(self, queries: list[str], k: int, threshold: float,
                     category: Optional[str] = None) -> list[RetrievedChunk]:
        """Search with several phrasings of the same question (e.g. Arabic + its English query).

        Each chunk keeps its best similarity over all queries; then threshold, near-duplicate
        removal and top-k apply as in search(). Timings are summed.
        """
        best: dict[str, RetrievedChunk] = {}
        embed_ms = search_ms = 0.0
        for q in queries:
            for h in self.search(q, k, threshold, category):
                if h.chunk_id not in best or h.similarity > best[h.chunk_id].similarity:
                    best[h.chunk_id] = h
            embed_ms += self.last_timing["embed"]
            search_ms += self.last_timing["search"]
        merged: list[RetrievedChunk] = []
        for h in sorted(best.values(), key=lambda x: x.similarity, reverse=True):
            if not any(_jaccard(h.text, m.text) >= self.dedup_jaccard for m in merged):
                merged.append(h)
        self.last_timing = {"embed": embed_ms, "search": search_ms}
        return merged[:k]


@lru_cache(maxsize=1)
def get_retriever() -> Retriever:
    """The shared Retriever for the configured collection (built once per process)."""
    from rag.embeddings import get_embedder

    s = get_settings()
    client = get_client()
    try:
        collection = client.get_collection(s.collection_name)
    except Exception as exc:  # noqa: BLE001 - Chroma raises different types across versions
        raise IndexNotReadyError(f"Collection '{s.collection_name}' not found. Run: "
                                 "python cli.py ingest, then python cli.py index") from exc
    embedder = get_embedder()
    _ = embedder.model  # load the model now, so its loading time is not counted as query latency
    return Retriever(embedder, collection)


def retrieve(query: str, k: Optional[int] = None, threshold: Optional[float] = None,
             category: Optional[str] = None) -> list[RetrievedChunk]:
    """Search the guideline index (stable Phase 2 interface).

    Args:
        query: the question, Arabic or English.
        k: number of chunks (default TOP_K from .env).
        threshold: minimum similarity (default SIMILARITY_THRESHOLD from .env).
        category: optional filter, e.g. "lactation".
    """
    s = get_settings()
    return get_retriever().search(query, k or s.top_k,
                                  s.similarity_threshold if threshold is None else threshold, category)


def format_for_tool(chunks: list[RetrievedChunk]) -> str:
    """Numbered plain-text context block used in the prompt (and as the Phase 2 tool output)."""
    if not chunks:
        return "No relevant guideline text was found."
    parts = []
    for i, c in enumerate(chunks, start=1):
        org_year = ", ".join(x for x in (c.organization, c.year) if x)
        parts.append(f"[{i}] Source: {c.title} ({org_year + '; ' if org_year else ''}{c.source}, "
                     f"page {c.page}; similarity {c.similarity:.2f})\n{c.text}")
    return "\n\n".join(parts)
