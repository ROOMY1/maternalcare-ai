"""Tests for safety/dosage_filter.py: >= 20 dose-positive and >= 20 must-not-block cases.

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import pytest

from safety.disclaimer import DOSAGE_NOTICE
from safety.dosage_filter import filter_dosage, find_doses
from tests.dosage_cases import DOSE_NEGATIVE, DOSE_POSITIVE


def test_enough_cases():
    assert len(DOSE_POSITIVE) >= 20 and len(DOSE_NEGATIVE) >= 20


@pytest.mark.parametrize("sentence", DOSE_POSITIVE)
def test_dose_sentences_are_detected(sentence):
    assert find_doses(sentence), f"missed dose in: {sentence}"


@pytest.mark.parametrize("sentence", DOSE_NEGATIVE)
def test_non_dose_numbers_are_not_blocked(sentence):
    assert not find_doses(sentence), f"false block: {sentence} -> {find_doses(sentence)}"


def test_only_dose_sentence_is_replaced_en():
    text = ("Paracetamol is commonly used in pregnancy [1]. Take 500 mg every 6 hours [1]. "
            "Discuss any medicine with your obstetrician [2]. Avoid NSAIDs after 20 weeks [3].")
    r = filter_dosage(text, "en")
    assert r.blocked and r.matches
    assert "500 mg" not in r.text and DOSAGE_NOTICE["en"] in r.text
    assert "Paracetamol is commonly used" in r.text and "Avoid NSAIDs after 20 weeks" in r.text
    assert "[1]. " + DOSAGE_NOTICE["en"] in r.text  # sentences stay separated by one space


def test_arabic_indic_digits_are_normalized():
    r = filter_dosage("الباراسيتامول مستخدم في الحمل. خدي ٥٠٠ مجم. اسألي دكتورك. تجنبي المسكنات التانية.", "ar")
    assert r.blocked and DOSAGE_NOTICE["ar"] in r.text and "٥٠٠" not in r.text


def test_mostly_doses_replaces_whole_answer():
    r = filter_dosage("Take 500 mg. Then 2 tablets twice daily. Ask your doctor.", "en")
    assert r.blocked and r.text == DOSAGE_NOTICE["en"]


def test_clean_answer_unchanged():
    text = "At 20 weeks an anomaly scan is offered [1]. Blood pressure of 140/90 mmHg is high [2]."
    r = filter_dosage(text, "en")
    assert not r.blocked and r.text == text and r.matches == []


@pytest.mark.parametrize("text,lang", [
    ("نعم، يمكنك الاستمرار في تناول جلوكوفاج أثناء الحمل، لكن يجب مناقشة هذا مع طبيبك [1].", "ar"),
    ("Yes, you can continue taking metformin during pregnancy [1].", "en"),
    ("استمري على الدواء زي ما هو.", "ar"),
    ("You should stop taking ibuprofen after 20 weeks.", "en"),
    ("ممكن تاخدي بنادول عادي.", "ar"),
    ("نعم، يمكن استمرار استخدام جلوكوفاج خلال الحمل [1][2].", "ar"),  # from the manual UI test
])
def test_medication_advice_removed(text, lang):
    from safety.dosage_filter import MEDICATION_ADVICE_NOTICE, filter_medication_advice

    r = filter_medication_advice(text, lang)
    assert r.blocked and MEDICATION_ADVICE_NOTICE[lang] in r.text


@pytest.mark.parametrize("text,lang", [
    ("يمكنك تناول السوشي المطبوخ [1].", "ar"),
    ("You can continue breastfeeding while ill [1].", "en"),
    ("FDA recommends avoiding NSAIDs at 20 weeks or later [1].", "en"),
    ("يجب مناقشة أي دواء مع طبيب النساء [1].", "ar"),
    ("Metformin has been studied in pregnancy [1].", "en"),
])
def test_medication_advice_not_over_blocked(text, lang):
    from safety.dosage_filter import filter_medication_advice

    assert not filter_medication_advice(text, lang).blocked


@pytest.mark.parametrize("text,lang,blocked", [
    ("قد يكون لديك تسمم الحمل أو التهاب الرحم بعد الولادة [1][2][3].", "ar", True),
    ("قد يكون لديك مرض الكيس النبوي (Cystitis) [2][3].", "ar", True),
    ("You may have pre-eclampsia [1].", "en", True),
    ("You might have an infection [2].", "en", True),
    ("These symptoms can be signs of pre-eclampsia and need urgent care [1].", "en", False),
    ("قد تكون هذه الأعراض علامة على تسمم الحمل وتحتاج رعاية عاجلة [1].", "ar", False),
    ("If you have diabetes, glucose should be monitored [1].", "en", False),
])
def test_diagnosis_filter(text, lang, blocked):
    from safety.dosage_filter import DIAGNOSIS_NOTICE, filter_diagnosis

    r = filter_diagnosis(text, lang)
    assert r.blocked == blocked
    if blocked:
        assert DIAGNOSIS_NOTICE[lang] in r.text


def test_decimal_point_is_not_a_sentence_break():
    r = filter_dosage("Iron supplements are advised [1]. A dose of 2.5 mg was studied [2]. Ask your doctor [1].", "en")
    assert r.blocked and "2." not in r.text and "Iron supplements are advised" in r.text
