"""The whole online RAG path in one function: answer_question().

Purpose
    retrieve -> (no chunks? return not-found, NO LLM call) -> grounded prompt -> local LLM
    -> dosage filter -> map [n] markers to citations -> add disclaimer -> RAGResponse.
The safety steps are enforced here in code, not only in the prompt.

Public interface (stable): answer_question(query, history=None) -> RAGResponse
`history` is accepted for the Phase 2 interface but not used: Phase 1 is single-turn.

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

from config import get_settings
from rag.prompts import build_messages
from rag.retriever import Retriever, format_for_tool, get_retriever
from rag.schemas import Citation, LatencyMs, RAGResponse, RetrievedChunk
from safety.arabic_text import detect_language, normalize, to_ascii_digits
from rag.query_transform import to_english_query
from safety.disclaimer import EMPTY_ANSWER, GENERATION_FAILED, add_disclaimer, get_emergency_banner, get_not_found
from safety.dosage_filter import filter_diagnosis, filter_dosage, filter_medication_advice
from safety.emergency_check import check_emergency

logger = logging.getLogger(__name__)

_MARKER = re.compile(r"\[\s*(\d+(?:\s*[,،]\s*\d+)*)\s*\]")


def map_citations(answer: str, chunks: list[RetrievedChunk]) -> tuple[str, list[Citation]]:
    """Turn [n] markers into citations. Invalid numbers (no such excerpt) are removed from the text.

    Returns (cleaned answer, citations in order of first use, without duplicates).
    """
    answer = to_ascii_digits(answer)
    used: list[int] = []

    def _fix(m: re.Match[str]) -> str:
        nums = [int(n) for n in re.split(r"\s*[,،]\s*", m.group(1))]
        valid = [n for n in nums if 1 <= n <= len(chunks)]
        for n in valid:
            if n not in used:
                used.append(n)
        return "".join(f"[{n}]" for n in valid)

    cleaned = _MARKER.sub(_fix, answer)
    cleaned = re.sub(r"[ \t]+([.,،؛])", r"\1", cleaned)  # space left before punctuation
    citations = [Citation(marker=n, source=chunks[n - 1].source, title=chunks[n - 1].title,
                          page=chunks[n - 1].page, similarity_score=chunks[n - 1].similarity)
                 for n in used]
    return cleaned.strip(), citations


def is_not_found_answer(answer: str, lang: str) -> bool:
    """True when the LLM replied with the not-found sentence.

    The sentence counts wherever it appears: an answer that says some things and then "I could not
    find an answer" is the model admitting that the context does not answer the question (seen with
    an off-topic context in the stress test), so it is treated as not found.
    """
    a = normalize(re.sub(r"\[\d+\]", "", answer)).strip(' ."')
    for lg in ("en", "ar"):
        nf = normalize(get_not_found(lg)).strip(' ."')  # type: ignore[arg-type]
        if nf in a:
            return True
    return False


def answer_question(query: str, history: Optional[list[Any]] = None, *,
                    retriever: Optional[Retriever] = None, llm: Any = None,
                    k: Optional[int] = None, threshold: Optional[float] = None,
                    translate: bool = True) -> RAGResponse:
    """Answer one question from the guidelines.

    Args:
        query: the user question (Arabic or English).
        history: ignored in Phase 1 (kept so Phase 2 can pass the conversation).
        retriever / llm: injected in tests; default = the shared Chroma retriever and ChatOllama.
        k / threshold: override TOP_K / SIMILARITY_THRESHOLD (used by the evaluation scripts).
        translate: for Arabic questions, also search with an English query written by the LLM.
    """
    del history  # Phase 1 is single-turn by design
    s = get_settings()
    retriever = retriever or get_retriever()  # one-time model/index loading is not query latency
    t_start = time.perf_counter()
    lang = detect_language(query)
    model_info = {"llm": s.llm_model, "embedding": s.embed_model}

    # 0. emergency check BEFORE retrieval, without the LLM (preliminary; full interceptor = Phase 2)
    emergency = check_emergency(query)
    if emergency.is_emergency:
        logger.info("emergency signs detected: %s", emergency.categories)

    def finish(body: str, **kw: Any) -> RAGResponse:
        """Add the emergency banner (if any) and the disclaimer by code, then build the response."""
        if emergency.is_emergency:
            body = f"{get_emergency_banner(lang)}\n\n{body}"
        lat.total = (time.perf_counter() - t_start) * 1000
        return RAGResponse(answer=add_disclaimer(body, lang), language=lang, latency_ms=lat,
                           model=model_info, emergency=emergency.is_emergency,
                           emergency_categories=emergency.categories, query_used=en_query or None, **kw)

    # 1. query transformation for Arabic: an English search query from the local LLM
    llm = llm or _default_llm()
    en_query = ""
    lat = LatencyMs()
    if lang == "ar" and translate:
        t_tr = time.perf_counter()
        en_query = to_english_query(query, llm)
        lat.transform = (time.perf_counter() - t_tr) * 1000

    # 2. retrieve with the original question (+ the English query), best score per chunk
    queries = [query] + ([en_query] if en_query else [])
    chunks = retriever.search_multi(queries, k or s.top_k,
                                    s.similarity_threshold if threshold is None else threshold)
    lat.embed, lat.search = retriever.last_timing["embed"], retriever.last_timing["search"]

    # 3. no chunk above the threshold -> fixed reply, the LLM is NOT called
    if not chunks:
        return finish(get_not_found(lang), not_found=True)

    # 4. grounded prompt, 5. LLM (timed). Full answer first, no streaming (filter must run first).
    messages = build_messages(query, format_for_tool(chunks), lang)
    t_llm = time.perf_counter()
    raw_text, gen_error = generate(llm, messages)
    if lang == "ar" and raw_text and has_foreign_script(raw_text):
        # one retry when the Arabic answer contains Cyrillic/other scripts or Latin letters glued
        # inside Arabic words (e.g. "فاطбиقي", "تiredness")
        logger.info("answer had mixed scripts; regenerating once")
        retry = build_messages(query, format_for_tool(chunks), lang, strict_script=True)
        raw_text = generate(llm, retry)[0] or raw_text
        if has_foreign_script(raw_text) and not is_not_found_answer(raw_text, lang):
            # still garbled after the strict retry: showing it would be worse than a clear message
            logger.warning("answer still had mixed scripts after the retry; not shown")
            gen_error = True
    lat.llm = (time.perf_counter() - t_llm) * 1000

    if gen_error:  # the model failed twice (e.g. "token repeat limit reached"): clear message
        return finish(GENERATION_FAILED[lang], retrieved=chunks, generation_failed=True)
    if not raw_text:  # an empty generation must never show as an empty chat bubble
        logger.warning("LLM returned an empty answer")
        return finish(EMPTY_ANSWER[lang], retrieved=chunks)

    if is_not_found_answer(raw_text, lang):
        return finish(get_not_found(lang), retrieved=chunks, not_found=True)

    # 6 (before 5). Markers are read from the raw answer so citations survive the filter.
    _, citations = map_citations(raw_text, chunks)
    # 5. dosage filter on every answer, before the user sees anything
    filtered = filter_dosage(raw_text, lang)
    if filtered.blocked:
        logger.info("dosage filter: %d dose expression(s) removed", len(filtered.matches))
    # 6b. start/continue/stop-a-medicine advice is removed in code too (the prompt alone failed)
    advice = filter_medication_advice(filtered.text, lang)
    if advice.blocked:
        logger.info("medication-advice filter: %d sentence(s) replaced", len(advice.matches))
    # 6c. "you may have X" diagnosis sentences are replaced in code too (the prompt alone failed)
    diagnosis = filter_diagnosis(advice.text, lang)
    if diagnosis.blocked:
        logger.info("diagnosis filter: %d sentence(s) replaced", len(diagnosis.matches))
    answer_text, _ = map_citations(diagnosis.text, chunks)  # drop invalid markers in the final text

    if not citations:  # model did not cite: show the chunks as "retrieved sources", not "cited"
        cited = False
        citations = [Citation(marker=None, source=c.source, title=c.title, page=c.page,
                              similarity_score=c.similarity) for c in chunks]
    else:
        cited = True

    # 8. banner + disclaimer added by code, 9. response
    return finish(answer_text, citations=citations, retrieved=chunks, cited=cited,
                  dosage_filtered=filtered.blocked,
                  advice_filtered=advice.blocked or diagnosis.blocked)


_LOOP = re.compile(r"(.{6,60}?)(?:\s*\1){3,}", re.S)


def looks_looped(text: str) -> bool:
    """True if the same piece of text (6-60 chars) repeats 4+ times in a row (a generation loop)."""
    return bool(_LOOP.search(text))


def generate(llm: Any, messages: list[Any]) -> tuple[str, bool]:
    """Call the LLM with one retry. Returns (text, failed).

    The first call uses the normal settings (num_predict and repeat_penalty from .env). If it raises
    (e.g. Ollama's "prediction aborted, token repeat limit reached") or the text loops, it is retried
    once with a stronger repeat penalty. If that also fails, failed=True and the caller shows a clear
    message instead of an error.
    """
    from rag.llm import with_limits

    s = get_settings()
    attempts = [llm, with_limits(llm, repeat_penalty=max(1.3, s.llm_repeat_penalty + 0.2))]
    for i, model in enumerate(attempts):
        try:
            text = str(getattr(model.invoke(messages), "content", "")).strip()
        except Exception as exc:  # noqa: BLE001 - any generation error gets one retry, then a message
            logger.warning("generation failed (attempt %d): %s", i + 1, type(exc).__name__)
            continue
        if looks_looped(text):
            logger.warning("generation looped (attempt %d)", i + 1)
            continue
        return text, False
    return "", True


_FOREIGN = re.compile(r"[Ѐ-ӿͰ-Ͽ一-鿿぀-ヿ֐-׿]")
_GLUED = re.compile(r"[ء-ي][A-Za-z]|[A-Za-z][ء-ي]")


def has_foreign_script(text: str) -> bool:
    """True if an Arabic answer contains Cyrillic/Greek/CJK/Hebrew letters, Latin letters glued to
    Arabic letters inside one word, or many separate English words (more than 5, e.g. "kondom ...
    outcomes outcome through relations"). A few Latin words (drug names, STI) are allowed."""
    latin_words = re.findall(r"\b[A-Za-z]{3,}\b", text)
    return bool(_FOREIGN.search(text) or _GLUED.search(text) or len(latin_words) > 5)


_LLM: Any = None


def _default_llm() -> Any:
    """Create the ChatOllama once and reuse it."""
    global _LLM
    if _LLM is None:
        from rag.llm import get_chat_model

        _LLM = get_chat_model()
    return _LLM
