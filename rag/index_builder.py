"""Step 4 of the pipeline (the "embedding script"): store chunk vectors in ChromaDB.

Purpose
    Read processed_chunks.json, embed every chunk with the Embedder and upsert it into a
    persistent Chroma collection that uses cosine distance.

Key properties
    - Idempotent: the chunk_id is the Chroma ID and we use upsert, so running it twice does not
      create duplicates.
    - --rebuild deletes and re-creates the collection (the CLI asks for confirmation).
    - The collection metadata records the embedding model and chunk settings; the retriever
      refuses to run if the configured embedding model differs (vectors from two different
      models are not comparable).
    - Telemetry is off (anonymized_telemetry=False).

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from config import PROCESSED_CHUNKS, VECTOR_DB_DIR, get_settings
from rag.ingestion import read_chunks
from rag.schemas import Chunk

logger = logging.getLogger(__name__)


class EmbedderLike(Protocol):
    model_name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


@dataclass
class IndexStats:
    chunks_in_file: int
    collection_count: int
    seconds: float
    sample_query: str = ""
    sample_hit: str = ""


def get_client(path: Path = VECTOR_DB_DIR) -> Any:
    """Persistent Chroma client on disk, with telemetry off."""
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(path), settings=ChromaSettings(anonymized_telemetry=False))


def create_collection(client: Any, name: str, embed_model: str, chunk_size: int,
                      chunk_overlap: int) -> Any:
    """Create (or open) the collection with cosine distance and our build metadata.

    Chroma's default distance is L2. In Chroma 1.x the distance is set with
    configuration={"hnsw": {"space": "cosine"}} (the old metadata={"hnsw:space": ...} key is
    the pre-1.0 way). Verified on the pinned version in check_env.py.
    """
    return client.get_or_create_collection(
        name=name,
        configuration={"hnsw": {"space": "cosine"}},
        metadata={
            "embed_model": embed_model,
            "chunk_size_tokens": chunk_size,
            "chunk_overlap_tokens": chunk_overlap,
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )


def collection_space(collection: Any) -> str:
    """Return the distance function of a collection ("cosine", "l2" or "ip")."""
    cfg = getattr(collection, "configuration", None) or {}
    hnsw = cfg.get("hnsw") if isinstance(cfg, dict) else None
    space = (hnsw or {}).get("space") if isinstance(hnsw, dict) else None
    return str(space or (collection.metadata or {}).get("hnsw:space") or "l2")


def _meta(c: Chunk) -> dict[str, Any]:
    """Chroma metadata must be str/int/float/bool (no None)."""
    return {"source": c.source, "title": c.title, "organization": c.organization, "year": c.year,
            "category": c.category, "page": c.page, "language": c.language, "n_tokens": c.n_tokens}


def build_index(chunks: list[Chunk], embedder: EmbedderLike, client: Any,
                collection_name: str, chunk_size: int, chunk_overlap: int,
                rebuild: bool = False, batch_size: int = 64) -> IndexStats:
    """Embed and upsert chunks. Returns stats after checking count == number of chunks."""
    start = time.perf_counter()
    if rebuild:
        try:
            client.delete_collection(collection_name)
            logger.info("Deleted collection %s", collection_name)
        except Exception:  # noqa: BLE001 - it did not exist
            pass
    col = create_collection(client, collection_name, embedder.model_name, chunk_size, chunk_overlap)
    built_with = (col.metadata or {}).get("embed_model")
    if built_with and built_with != embedder.model_name:
        raise RuntimeError(
            f"Collection was built with {built_with} but EMBED_MODEL is {embedder.model_name}. "
            "Run: python cli.py index --rebuild --yes")

    # Remove IDs that are no longer in the chunk file (e.g. a PDF was removed or re-chunked)
    wanted = {c.chunk_id for c in chunks}
    existing = set(col.get(include=[])["ids"])
    stale = sorted(existing - wanted)
    if stale:
        col.delete(ids=stale)
        logger.info("Removed %d stale chunks", len(stale))

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        col.upsert(
            ids=[c.chunk_id for c in batch],
            documents=[c.text for c in batch],
            metadatas=[_meta(c) for c in batch],
            embeddings=embedder.embed_documents([c.text for c in batch]),
        )
        logger.info("Upserted %d/%d", min(i + batch_size, len(chunks)), len(chunks))

    count = col.count()
    if count != len(wanted):
        raise RuntimeError(f"Index check failed: collection has {count}, file has {len(wanted)}")
    return IndexStats(chunks_in_file=len(chunks), collection_count=count,
                      seconds=time.perf_counter() - start)


def build_from_file(rebuild: bool = False, path: Path = PROCESSED_CHUNKS,
                    sample_query: str = "What are the warning signs of pre-eclampsia?") -> IndexStats:
    """CLI entry point: processed_chunks.json -> Chroma, then one sample query as a smoke test."""
    from rag.embeddings import get_embedder

    s = get_settings()
    header, chunks = read_chunks(path)
    if header.get("embed_model") != s.embed_model:
        raise RuntimeError(f"processed_chunks.json was tokenized for {header.get('embed_model')} "
                           f"but EMBED_MODEL is {s.embed_model}. Run: python cli.py ingest")
    embedder = get_embedder()
    client = get_client()
    stats = build_index(chunks, embedder, client, s.collection_name,
                        header["chunk_size_tokens"], header["chunk_overlap_tokens"], rebuild=rebuild)
    col = client.get_collection(s.collection_name)
    res = col.query(query_embeddings=[embedder.embed_query(sample_query)], n_results=1,
                    include=["metadatas", "distances"])
    if res["ids"][0]:
        m, d = res["metadatas"][0][0], res["distances"][0][0]
        stats.sample_query = sample_query
        stats.sample_hit = f"{m['source']} p.{m['page']} (similarity {1 - d:.3f})"
    return stats
