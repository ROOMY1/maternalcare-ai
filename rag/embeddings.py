"""Step 3 of the pipeline: turn text into vectors with a local embedding model.

Purpose
    Wrap sentence-transformers so the rest of the code calls embed_documents()/embed_query()
    and never worries about the device, batching, normalization or model-specific prefixes.

Details
    - The model is loaded once, on first use (lazy singleton through get_embedder()).
    - Device: EMBED_DEVICE=auto picks cuda, then Apple mps, then cpu. On cuda the model runs
      in fp16 (half the VRAM, same ranking quality for retrieval).
    - Vectors are L2-normalized, so cosine similarity = dot product.
    - BAAI/bge-m3 needs no prefixes. E5 models need "query: " / "passage: "; they are added here,
      at embedding time only, so the stored chunk text stays clean.

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)


def resolve_device(requested: str = "auto") -> str:
    """Return "cuda", "mps" or "cpu" for the requested setting."""
    if requested != "auto":
        return requested
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class Embedder:
    """Local sentence-transformers embedder (bge-m3 by default)."""

    def __init__(self, model_name: str | None = None, device: str | None = None,
                 batch_size: int | None = None) -> None:
        s = get_settings()
        self.model_name = model_name or s.embed_model
        self.device = resolve_device(device or s.embed_device)
        self.batch_size = batch_size or s.embed_batch_size
        self._model: Any = None

    @property
    def is_e5(self) -> bool:
        return "e5" in self.model_name.lower()

    @property
    def model(self) -> Any:
        """Load the model the first time it is needed."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model %s on %s", self.model_name, self.device)
            kwargs: dict[str, Any] = {}
            if self.device == "cuda":
                import torch

                kwargs["model_kwargs"] = {"torch_dtype": torch.float16}
            self._model = SentenceTransformer(self.model_name, device=self.device, **kwargs)
        return self._model

    @property
    def dimension(self) -> int:
        # sentence-transformers 6.x renamed get_sentence_embedding_dimension -> get_embedding_dimension
        getter = getattr(self.model, "get_embedding_dimension", None) or \
            self.model.get_sentence_embedding_dimension
        return int(getter())

    @property
    def max_seq_length(self) -> int:
        return int(self.model.max_seq_length)

    def _encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(
            texts, batch_size=self.batch_size, normalize_embeddings=True,
            convert_to_numpy=True, show_progress_bar=len(texts) > 64,
        )
        return vectors.astype("float32").tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed chunk texts (adds "passage: " only for E5 models)."""
        if self.is_e5:
            texts = [f"passage: {t}" for t in texts]
        return self._encode(texts)

    def embed_query(self, text: str) -> list[float]:
        """Embed one question (adds "query: " only for E5 models)."""
        return self._encode([f"query: {text}" if self.is_e5 else text])[0]


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    """The shared Embedder (one model in memory per process)."""
    return Embedder()
