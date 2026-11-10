"""Tests for rag/rag_pipeline.py with a fake LLM and a fake retriever (no Ollama, no model).

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import pytest

from rag.rag_pipeline import answer_question, map_citations
from rag.schemas import RetrievedChunk
from safety.disclaimer import DISCLAIMER, DOSAGE_NOTICE, EMERGENCY_BANNER, EMPTY_ANSWER, NOT_FOUND


def _chunk(i: int) -> RetrievedChunk:
    return RetrievedChunk(chunk_id=f"c{i}", text=f"text {i}", source=f"doc{i}.pdf", title=f"Doc {i}",
                          page=i + 10, category="antenatal_care", similarity=0.8 - i * 0.05)


class FakeRetriever:
    def __init__(self, chunks):
        self.chunks = chunks
        self.last_timing = {"embed": 1.0, "search": 2.0}

    def search(self, query, k, threshold, category=None):
        return self.chunks[:k]

    def search_multi(self, queries, k, threshold, category=None):
        self.queries = list(queries)
        return self.chunks[:k]


class FakeLLM:
    """Returns the replies in order (the last one repeats). Records every call."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        reply = self.replies[min(self.calls - 1, len(self.replies) - 1)]

        class Msg:
            content = reply

        return Msg()


def test_no_context_does_not_call_llm():
    llm = FakeLLM("should not be used")
    r = answer_question("What is the capital of France?", retriever=FakeRetriever([]), llm=llm)
    assert llm.calls == 0
    assert r.not_found and r.citations == [] and NOT_FOUND["en"] in r.answer
    assert DISCLAIMER["en"] in r.answer


def test_arabic_no_context_reply_in_arabic():
    llm = FakeLLM("Who won the World Cup?")
    r = answer_question("مين كسب كاس العالم؟", retriever=FakeRetriever([]), llm=llm)
    assert r.language == "ar" and NOT_FOUND["ar"] in r.answer and DISCLAIMER["ar"] in r.answer
    assert llm.calls == 1  # only the query rewrite; the answer generation is never called
    assert r.latency_ms.llm == 0


def test_arabic_question_also_searched_in_english():
    ret = FakeRetriever([_chunk(0)])
    r = answer_question("التونه كويسه وانا حامل؟", retriever=ret,
                        llm=FakeLLM("tuna mercury pregnancy", "التونة [1]."))
    assert ret.queries == ["التونه كويسه وانا حامل؟", "tuna mercury pregnancy"]
    assert r.query_used == "tuna mercury pregnancy" and r.latency_ms.transform >= 0


def test_emergency_banner_even_when_not_found():
    r = answer_question("بعد الولاده عندي نزيف كتير اوي", retriever=FakeRetriever([]),
                        llm=FakeLLM("heavy bleeding after birth"))
    assert r.emergency and "bleeding" in r.emergency_categories
    assert r.answer.startswith(EMERGENCY_BANNER["ar"]) and r.not_found


def test_emergency_banner_with_answer_en():
    r = answer_question("My water broke at 34 weeks, what should I do?",
                        retriever=FakeRetriever([_chunk(0)]), llm=FakeLLM("Go to hospital [1]."))
    assert r.emergency and r.answer.startswith(EMERGENCY_BANNER["en"]) and "[1]" in r.answer


def test_no_banner_for_normal_question():
    r = answer_question("Is iron recommended?", retriever=FakeRetriever([_chunk(0)]), llm=FakeLLM("Yes [1]."))
    assert not r.emergency and EMERGENCY_BANNER["en"] not in r.answer


def test_empty_llm_answer_gives_clear_message():
    r = answer_question("Is iron recommended?", retriever=FakeRetriever([_chunk(0)]), llm=FakeLLM("   "))
    assert EMPTY_ANSWER["en"] in r.answer


def test_foreign_script_answer_is_regenerated_once():
    llm = FakeLLM("iron in pregnancy", "فاطбиقي الحديد [1].", "الحديد مهم [1].")
    r = answer_question("هل الحديد مهم للحامل؟", retriever=FakeRetriever([_chunk(0)]), llm=llm)
    assert llm.calls == 3 and "б" not in r.answer and "الحديد مهم" in r.answer


REPEAT_ERROR = "prediction aborted, token repeat limit reached (status code: -1)"


class RaisingLLM:
    """Raises the Ollama repeat-limit error for the first `fail_times` calls, then answers."""

    def __init__(self, fail_times: int, reply: str = "Answer [1]."):
        self.fail_times, self.reply, self.calls = fail_times, reply, 0

    def invoke(self, messages):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(REPEAT_ERROR)

        class Msg:
            content = self.reply

        return Msg()


def test_repeat_error_everywhere_gives_clear_message_not_crash():
    """The UI error 'token repeat limit reached': rewrite AND answer fail -> clear message."""
    from safety.disclaimer import GENERATION_FAILED

    llm = RaisingLLM(fail_times=99)
    r = answer_question("عادي اكون مع زوجي في علاقه حميمه في فتره الحمل",
                        retriever=FakeRetriever([_chunk(0)]), llm=llm)
    assert r.query_used is None            # rewrite failed -> searched with the Arabic question only
    assert r.generation_failed and GENERATION_FAILED["ar"] in r.answer
    assert llm.calls == 3                  # 1 rewrite + 2 answer attempts, then stop


def test_repeat_error_in_rewrite_only_still_answers():
    r = answer_question("هل الحديد مهم للحامل؟", retriever=FakeRetriever([_chunk(0)]),
                        llm=RaisingLLM(fail_times=1, reply="الحديد مهم [1]."))
    assert r.query_used is None and not r.generation_failed and "الحديد مهم" in r.answer


def test_repeat_error_once_in_answer_is_retried():
    r = answer_question("Is iron recommended?", retriever=FakeRetriever([_chunk(0)]),
                        llm=RaisingLLM(fail_times=1, reply="Iron is recommended [1]."))
    assert not r.generation_failed and "Iron is recommended" in r.answer


def test_looping_answer_is_retried():
    loop = "the dose the dose the dose the dose the dose the dose the dose the dose"
    r = answer_question("Is iron recommended?", retriever=FakeRetriever([_chunk(0)]),
                        llm=FakeLLM(loop, "Iron is recommended [1]."))
    assert "Iron is recommended" in r.answer and "the dose the dose" not in r.answer


def test_still_garbled_after_retry_is_not_shown():
    from safety.disclaimer import GENERATION_FAILED

    garbled = "استخدام kondom outcomes outcome via relations kondoms STIs hepatitis [1]."
    r = answer_question("سؤال عن العلاقة في الحمل", retriever=FakeRetriever([_chunk(0)]),
                        llm=FakeLLM("sexual relations during pregnancy", garbled))
    assert r.generation_failed and GENERATION_FAILED["ar"] in r.answer and "kondom" not in r.answer


def test_answer_ending_with_not_found_is_not_found():
    text = "من المهم استخدام kondom أثناء العلاقة [1]. " + NOT_FOUND["ar"]
    r = answer_question("سؤال عن العلاقة في الحمل", retriever=FakeRetriever([_chunk(0)]),
                        llm=FakeLLM("sexual relations during pregnancy", text))
    assert r.not_found and r.citations == []


def test_many_english_words_in_arabic_answer_trigger_regeneration():
    from rag.rag_pipeline import has_foreign_script

    assert has_foreign_script("لحمايتك من الأمراض المنقولة outcomes outcome through relations kondoms STIs hepatitis")
    assert not has_foreign_script("الإيبوبروفين (ibuprofen) من مضادات الالتهاب NSAIDs [1].")


@pytest.mark.parametrize("raw,expected", [
    ("sexual relations during pregnancy", "sexual relations during pregnancy"),
    ("Query: tuna mercury pregnancy", "tuna mercury pregnancy"),
    ("", ""),
    ("   ", ""),
    ("التونة في الحمل", ""),                                   # not translated
    ("pregnancy pregnancy pregnancy pregnancy", ""),             # repeating
    ("word " * 40, ""),                                         # far too long
])
def test_clean_query(raw, expected):
    from rag.query_transform import clean_query

    assert clean_query(raw) == expected


def test_disclaimer_always_added_and_citations_mapped():
    llm = FakeLLM("Iron is recommended [1]. Rest helps [2][7]. Also [1, 2].")
    r = answer_question("Is iron recommended?", retriever=FakeRetriever([_chunk(0), _chunk(1)]), llm=llm)
    assert llm.calls == 1 and r.answer.endswith(DISCLAIMER["en"])
    assert "[7]" not in r.answer  # invalid marker removed
    assert [c.marker for c in r.citations] == [1, 2] and r.cited
    assert r.citations[0].source == "doc0.pdf" and r.citations[0].page == 10


def test_dosage_filter_applied_before_return():
    llm = FakeLLM("Paracetamol is used in pregnancy [1]. Take 500 mg four times a day [1]. "
                  "Discuss it with your obstetrician [2]. Avoid self-medication [2].")
    r = answer_question("paracetamol dose?", retriever=FakeRetriever([_chunk(0), _chunk(1)]), llm=llm)
    assert r.dosage_filtered and "500 mg" not in r.answer and DOSAGE_NOTICE["en"] in r.answer
    assert [c.marker for c in r.citations] == [1, 2]  # citations kept


def test_no_markers_shows_retrieved_sources():
    r = answer_question("q?", retriever=FakeRetriever([_chunk(0)]), llm=FakeLLM("An answer without markers."))
    assert not r.cited and r.citations[0].marker is None


def test_llm_not_found_reply_is_detected():
    r = answer_question("q?", retriever=FakeRetriever([_chunk(0)]), llm=FakeLLM(NOT_FOUND["en"]))
    assert r.not_found and r.citations == []


def test_map_citations_arabic_digits():
    text, cites = map_citations("الحديد مهم [١].", [_chunk(0)])
    assert text == "الحديد مهم [1]." and cites[0].marker == 1


@pytest.mark.integration
def test_live_answer_with_ollama():
    """Needs Ollama + the index: pytest -m integration"""
    r = answer_question("What are the warning signs of pre-eclampsia?")
    assert r.answer and r.latency_ms.total > 0
