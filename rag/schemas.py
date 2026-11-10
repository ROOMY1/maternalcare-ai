"""Pydantic data models shared by the whole Phase 1 pipeline.

Purpose
    One definition of every object that moves between steps, so each file agrees on field
    names and types: a stored chunk, a retrieved chunk, a citation and the final response.

Author: Abdelrahman Islam.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    """One piece of a guideline page, as written to processed_chunks.json and stored in Chroma."""

    chunk_id: str
    text: str
    source: str  # PDF file name, e.g. WHO_antenatal-care_2016.pdf
    title: str
    organization: str
    year: str
    category: str
    page: int = Field(ge=1)  # 1-based, as printed in a PDF viewer
    language: Literal["ar", "en"]
    n_tokens: int = Field(ge=0)


class RetrievedChunk(BaseModel):
    """A chunk returned by the retriever, with its similarity to the question."""

    chunk_id: str
    text: str
    source: str
    title: str
    page: int
    category: str
    organization: Optional[str] = None
    year: Optional[str] = None
    similarity: float = Field(ge=0.0, le=1.0)  # 1 - cosine distance; NOT a confidence


class Citation(BaseModel):
    """A source shown under an answer."""

    marker: Optional[int] = None  # the [n] used in the answer; None for "retrieved, not cited"
    source: str
    title: str
    page: int
    similarity_score: float


class LatencyMs(BaseModel):
    """Time spent in each stage, in milliseconds."""

    transform: float = 0.0  # writing the English search query for an Arabic question
    embed: float = 0.0
    search: float = 0.0
    llm: float = 0.0
    total: float = 0.0


class RAGResponse(BaseModel):
    """The full Phase 1 answer object."""

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    retrieved: list[RetrievedChunk] = Field(default_factory=list)
    cited: bool = False  # True when the answer used [n] markers; False -> show as "retrieved sources"
    language: Literal["ar", "en"]
    not_found: bool = False
    dosage_filtered: bool = False
    advice_filtered: bool = False  # a medicine-advice or "you may have X" sentence was replaced
    generation_failed: bool = False  # the LLM failed twice; a clear message was returned
    emergency: bool = False  # a danger sign was found by safety/emergency_check.py (banner shown)
    emergency_categories: list[str] = Field(default_factory=list)
    query_used: Optional[str] = None  # English search query generated for Arabic questions
    latency_ms: LatencyMs = Field(default_factory=LatencyMs)
    model: dict[str, str] = Field(default_factory=dict)
