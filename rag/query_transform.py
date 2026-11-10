"""Query transformation for Arabic questions (assignment §2.4 "query transformation").

Purpose
    bge-m3 is multilingual, but colloquial Egyptian Arabic ("التونه كويسه ولا لا وانا حامل") often
    lands far from the English guideline text, so the question fell below the similarity threshold
    and got "not found", while the same question in English was answered. Here the local LLM rewrites
    an Arabic question as a short English medical search query. The retriever then searches with BOTH
    the original question and the English query and keeps the best score for each chunk.

    The rewritten query is only used for SEARCHING. The answer is still generated for the original
    question, in Arabic, from the retrieved guideline text.

Robustness (added after a "token repeat limit reached" error in the UI)
    The rewrite runs with a small token budget (REWRITE_NUM_PREDICT, default 48) and a repeat
    penalty. If the model raises an error, returns nothing, returns something too long, returns
    Arabic instead of English, or repeats itself, the function returns "" and the pipeline simply
    searches with the original Arabic question. The UI never fails because of this step.

Public interface: to_english_query(question, llm) -> str  ("" = no usable rewrite)

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from config import get_settings
from rag.llm import with_limits

logger = logging.getLogger(__name__)

SYSTEM = (
    "You convert a question written in Arabic or Egyptian Arabic dialect into ONE short English "
    "search query for a database of pregnancy and maternal-care guidelines. Use standard medical "
    "terms (e.g. 'reduced fetal movements', 'breast milk supply', 'listeria', 'fetal heart rate "
    "monitoring in labour', 'NSAIDs in pregnancy'). Keep drug names as they are, in Latin letters. "
    "Do NOT answer the question. Output only the query, at most 20 words, nothing else. If the "
    "question is not about health, just translate it literally."
)
MAX_CHARS = 200
MAX_WORDS = 30
_ARABIC = re.compile(r"[؀-ۿ]")
_REPEAT = re.compile(r"\b(\w+(?:\s+\w+){0,3})(?:\s+\1\b){2,}", re.IGNORECASE)


def clean_query(text: str) -> str:
    """Validate a model-written query. Returns "" when it is not usable."""
    text = (text or "").strip()
    if not text:
        return ""
    text = text.splitlines()[0]
    text = re.sub(r"^(?:query|search query|english query)\s*:\s*", "", text, flags=re.IGNORECASE)
    text = text.strip(" \"'`")
    if not text or len(text) > MAX_CHARS or len(text.split()) > MAX_WORDS:
        return ""  # abnormally long: probably the model answered or looped
    if _ARABIC.search(text) or _REPEAT.search(text):
        return ""  # not translated, or the same words repeated
    return text


def to_english_query(question: str, llm: Any) -> str:
    """Return a short English search query for an Arabic question ("" if anything goes wrong)."""
    s = get_settings()
    limited = with_limits(llm, num_predict=s.rewrite_num_predict, repeat_penalty=1.2)
    try:
        out = limited.invoke([SystemMessage(content=SYSTEM), HumanMessage(content=question)])
    except Exception as exc:  # noqa: BLE001 - search still works with the original question
        logger.warning("query rewrite failed (%s); searching with the original question only",
                       type(exc).__name__)
        return ""
    query = clean_query(str(getattr(out, "content", out)))
    if not query:
        logger.info("query rewrite unusable; searching with the original question only")
    return query
