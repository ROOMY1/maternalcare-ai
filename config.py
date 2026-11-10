"""Central settings for MaternalCare AI (Phase 1, local RAG).

Purpose
    One place for every setting (model names, chunk sizes, thresholds, paths). Values come from
    `.env` (see `.env.example`) through Pydantic `BaseSettings`, so nothing is hard-coded in the
    other modules. All paths are anchored to BASE_DIR (the folder of this file), never to the
    current working directory, so scripts work from any folder.

Inputs:  the `.env` file and environment variables.
Outputs: `get_settings()` -> Settings (cached), plus path constants.

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR: Path = Path(__file__).resolve().parent

DATA_DIR: Path = BASE_DIR / "data"
RAW_DIR: Path = DATA_DIR / "raw"
SOURCES_CSV: Path = DATA_DIR / "sources.csv"
PROCESSED_DIR: Path = DATA_DIR / "processed"
PROCESSED_CHUNKS: Path = PROCESSED_DIR / "processed_chunks.json"
VECTOR_DB_DIR: Path = BASE_DIR / "vector_db"
DB_DIR: Path = BASE_DIR / "db"
PATIENT_DB_PATH: Path = DB_DIR / "patient_records.db"
EVAL_DIR: Path = BASE_DIR / "eval"
RESULTS_DIR: Path = EVAL_DIR / "results"

# Max input length (tokens) of each supported embedding model, from the model cards:
#   https://huggingface.co/BAAI/bge-m3                     -> 8192
#   https://huggingface.co/intfloat/multilingual-e5-base   -> 512
EMBED_MODEL_MAX_TOKENS: dict[str, int] = {
    "BAAI/bge-m3": 8192,
    "intfloat/multilingual-e5-base": 512,
}


class Settings(BaseSettings):
    """All tunable settings. Field names map to upper-case variables in `.env`."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- LLM (Ollama) ---
    # 127.0.0.1, not "localhost": on Windows "localhost" is tried as IPv6 (::1) first, Ollama listens
    # on IPv4 only, and every new connection then waits ~2 s before falling back (measured).
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen2.5:7b-instruct"
    llm_temperature: float = 0.0
    llm_num_ctx: int = 8192  # Ollama's default context is small and silently cuts the context
    llm_keep_alive: str = "30m"  # keep the model in VRAM between questions
    # Generation limits: stop runaway / repeating output (Ollama aborts with "token repeat limit
    # reached" otherwise). The answer is <= ~150 words, the search query <= 20 words.
    llm_num_predict: int = 512
    llm_repeat_penalty: float = 1.1
    rewrite_num_predict: int = 48
    judge_model: str = "qwen2.5:7b-instruct"

    # --- Embeddings ---
    embed_model: str = "BAAI/bge-m3"
    embed_device: Literal["auto", "cuda", "mps", "cpu"] = "auto"
    embed_batch_size: int = 16

    # --- Chunking ---
    chunk_size_tokens: int = Field(400, gt=0)
    chunk_overlap_tokens: int = Field(60, ge=0)

    # --- Retrieval ---
    top_k: int = Field(4, gt=0)
    similarity_threshold: float = Field(0.45, ge=0.0, le=1.0)
    collection_name: str = "maternal_guidelines"

    # --- Misc ---
    hf_hub_offline: int = 0
    enable_hybrid: bool = False
    enable_reranker: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings and apply the privacy-related environment variables."""
    settings = Settings()
    # Privacy: no telemetry, and no Hugging Face network calls after download.
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    if settings.hf_hub_offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    return settings
