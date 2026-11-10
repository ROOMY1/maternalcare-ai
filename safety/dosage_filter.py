"""Post-generation dosage filter (second layer of the dosage guardrail).

Purpose
    The prompt already forbids doses, but an LLM can still write one. This filter runs on
    every answer after generation and before the user sees it. It finds "a number followed by
    a dose unit or a frequency" in English and Arabic, and replaces each sentence that contains
    one with a fixed safe sentence. Numbers that are NOT doses (pregnancy weeks, blood pressure,
    temperatures, lab values such as mg/dL or mmol/L, body weight, percentages, years and
    blood-loss volumes) are left alone.

Public interface (stable, reused as the last node of the Phase 2 graph):
    filter_dosage(text: str, lang: str) -> FilterResult(text, blocked, matches)
It is a pure function: no I/O, no logging, no global state. The caller logs the event.

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from safety.arabic_text import to_ascii_digits
from safety.disclaimer import get_dosage_notice

# ---------------------------------------------------------------- building blocks
_AR = r"؀-ۿ"  # Arabic block, used for "not followed by another Arabic letter"
_NUM = r"\d+(?:[.,]\d+)?"
# "1-2", "500 to 1000", "1 أو 2"
_NUM_RANGE = rf"{_NUM}(?:\s*(?:-|–|to|or|إلى|الى|او|أو)\s*{_NUM})?"
_EN_WORD_NUM = r"(?:one|two|three|four|five|six|half\s+a|a\s+half)"

# English dose units. mg is excluded when it is part of a lab unit (mg/dL, mg/L, mg/mmol, mg/24h).
_EN_UNIT = (
    r"(?:mg(?!\s*/\s*(?:d?l\b|mmol|g\b|24|day\b))(?:\s*/\s*kg)?"
    r"|mcg|µg|μg|ug|milligrams?|micrograms?"
    r"|m[lL]\b|millilit(?:er|re)s?|IU\b|i\.u\.|units?\b"
    r"|tablets?|tabs?\b|capsules?|caps\b|pills?|drops?|puffs?|sachets?"
    r"|teaspoons?|tsp\b|tablespoons?|tbsp\b"
    # grams: "1.5–2.0g", "2 grams"; not g/dL or g/L (lab values). OGTT glucose loads are
    # excluded by context in find_doses().
    r"|g\b(?!\s*/)|grams?\b(?!\s*/))"
)
_EN_DOSE = re.compile(rf"(?<![\w/.]){_NUM_RANGE}\s*-?\s*{_EN_UNIT}", re.IGNORECASE)
_EN_WORD_DOSE = re.compile(
    rf"\b{_EN_WORD_NUM}\s+(?:tablets?|capsules?|pills?|drops?|puffs?|sachets?|teaspoons?|tablespoons?)\b",
    re.IGNORECASE,
)
_EN_FREQ = re.compile(
    r"\b(?:\d+|one|two|three|four|five|six)\s*(?:times?|x)\s*(?:a|per|each)?\s*(?:day|daily)\b"
    r"|\b(?:once|twice|thrice)\s+(?:a\s+|per\s+|each\s+)?(?:day|daily)\b"
    r"|\bevery\s+\d+(?:\s*(?:-|to)\s*\d+)?\s*(?:hours?|hrs?|h)\b"
    r"|\bq\s?\d+\s?h\b"
    # schedules split into doses: "divided into three doses", "in 2 divided doses", "3 doses a day"
    r"|\bdivided\s+into\s+(?:\d+|two|three|four|five|six)\s+(?:doses|portions)\b"
    r"|\b(?:\d+|two|three|four|five|six)\s+divided\s+doses\b"
    r"|\b(?:\d+|two|three|four|five|six)\s+doses\s+(?:a|per|each)\s+day\b",
    re.IGNORECASE,
)
# "twice" / "three times" alone is a schedule only when the sentence is about a medicine
# ("take it three times") — "the risk is three times higher" must pass.
_EN_TIMES = re.compile(r"\b(?:twice|thrice|(?:\d+|two|three|four|five|six)\s+times)\b", re.IGNORECASE)

# Arabic dose units (written after the number). مل is blocked only when it is not "مل زئبق".
_AR_UNIT = (
    r"(?:مجم|ملجم|ملغم|ملغ|مغ|مليجرام|ملليجرام|ميليجرام|ميكروجرام|مكجم"
    r"|ميليغرام|ملليغرام|مليغرام|ميلي\s?غرام|ملي\s?غرام|ميلي\s?جرام|ملي\s?جرام|ميكروغرام"
    r"|غرامات|غرام|غم|جرامات|جرام|جم"
    r"|مل(?!\s*زئبق)|مللي(?!\s*(?:زئبق|متر|مول))|ملل(?!\s*زئبق)"
    r"|وحدة|وحده|وحدات|قرص|أقراص|اقراص|حبة|حبه|حبات|حباية|حبايات"
    r"|كبسولة|كبسوله|كبسولات|برشامة|برشامه|برشام|نقطة|نقطه|نقط"
    r"|بخة|بخه|بخات|ملعقة|ملعقه|معلقة|معلقه|كيس|أكياس|اكياس)"
)
# (?!\s*/) excludes lab units written in Arabic, e.g. "92 مجم/ديسيلتر" (mg/dL).
_AR_DOSE = re.compile(rf"(?<![\w/.]){_NUM_RANGE}\s*{_AR_UNIT}(?![{_AR}])(?!\s*/)")
# Dual forms are dose amounts by themselves: "قرصين" = two tablets, "حبتين" = two pills.
_AR_DUAL = re.compile(
    rf"(?<![{_AR}])(?:قرصين|حبتين|حبايتين|كبسولتين|برشامتين|نقطتين|بختين|ملعقتين|معلقتين)(?![{_AR}])"
)
_AR_ONE_UNIT = re.compile(
    rf"(?<![{_AR}])(?:قرص|حبة|حبه|حباية|كبسولة|كبسوله|برشامة|ملعقة|معلقة)\s+(?:واحد|واحدة|واحده)(?![{_AR}])"
)
_AR_PER_DAY = r"(?:يوميا|يومياً|يوميًا|يومي|في\s+اليوم|فى\s+اليوم|ف\s+اليوم|باليوم|كل\s+يوم)"
_AR_FREQ = re.compile(
    rf"(?:(?:\d+|ثلاث|تلات|أربع|اربع|خمس)\s*(?:مرات|مرة|مره)|مرتين|مرة|مره)"
    rf"(?:\s+(?:واحدة|واحده))?\s*{_AR_PER_DAY}"
    rf"|كل\s+\d+(?:\s*(?:-|إلى|الى)\s*\d+)?\s*(?:ساعات|ساعة|ساعه)"
    rf"|كل\s+ساعتين"
    # split into doses: "على 3 جرعات", "ثلاث جرعات", "جرعتين في اليوم"
    rf"|(?<![{_AR}])(?:(?:\d+|ثلاث|تلات|أربع|اربع|خمس)\s*جرعات|جرعتين)(?![{_AR}])"
)
# A gram amount next to these words is a glucose-test load (OGTT "75 g"), not a dose.
_GLUCOSE_TEST_CONTEXT = re.compile(
    r"glucose|OGTT|tolerance|load|سكر|جلوكوز|الجلوكوز|غلوكوز|الغلوكوز|تحمل", re.IGNORECASE)

# Context exclusions -------------------------------------------------------------
# A volume in mL next to blood/loss is a clinical definition (e.g. PPH = blood loss >= 500 mL).
_BLOOD_CONTEXT = re.compile(
    rf"blood|loss|bleed|haemorrhage|hemorrhage|نزف|نزيف|(?<![{_AR}])(?:ال)?دم(?![{_AR}])", re.IGNORECASE
)
# An amount in mg next to these words is a lab value (e.g. "300 mg per 24 hours" proteinuria).
_LAB_CONTEXT = re.compile(r"per\s+24|/\s*24|in\s+24\s*h|protein|urine|creatinine|بروتين|بول",
                          re.IGNORECASE)
# A frequency inside a monitoring sentence ("measure BP every 4 hours") is not a dose schedule.
_MONITOR_CONTEXT = re.compile(
    r"measur|monitor|check|record|assess|observ|count|auscultat|visit|contact|appointment"
    r"|قياس|قيس|اقيس|متابع|راقب|مراقب|عد\b|عدي|زيار|موعد",
    re.IGNORECASE,
)
# A medicine-related word makes a frequency a dose schedule even in a monitoring sentence.
_MEDICINE_CONTEXT = re.compile(
    r"\btake|dose|tablet|pill|capsule|medic|drug|خد|خدي|تاخد|تاخدي|جرعة|جرعه|دواء|دوا|علاج|قرص|حبوب|حبة",
    re.IGNORECASE,
)

_SENTENCE_END = re.compile(r"[.!?؟؛]+(?=\s)|\n+")


@dataclass(frozen=True)
class FilterResult:
    """Output of `filter_dosage`.

    text:    the answer after filtering (unchanged when nothing was blocked)
    blocked: True when at least one dose sentence was removed
    matches: the dose expressions that were found (for logs and tests)
    """

    text: str
    blocked: bool
    matches: list[str] = field(default_factory=list)


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Split into sentence spans (start, end). A '.' inside '2.5' is not a boundary (no space after)."""
    spans, start = [], 0
    for m in _SENTENCE_END.finditer(text):
        spans.append((start, m.end()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return [(s, e) for s, e in spans if text[s:e].strip()]


def find_doses(text: str) -> list[str]:
    """Return every dose expression in `text` (digits are normalized to ASCII first)."""
    norm = to_ascii_digits(text)
    found: list[str] = []
    for rx in (_EN_DOSE, _AR_DOSE):
        for m in rx.finditer(norm):
            is_volume = re.search(r"m[lL]|millilit|مل", m.group(0)) is not None
            window = norm[max(0, m.start() - 40) : m.end() + 40]
            if is_volume and _BLOOD_CONTEXT.search(window):
                continue
            if _LAB_CONTEXT.search(window):
                continue
            unit = m.group(0)
            is_gram = (re.search(r"(?:\d|\s|-)(?:g|grams?)$|غرام$|غم$|جرام$|جم$|غرامات$|جرامات$", unit, re.I)
                       is not None and re.search(r"مل|ميل|ميكرو|mg|mcg", unit, re.I) is None)
            if is_gram and _GLUCOSE_TEST_CONTEXT.search(window):
                continue  # "75 g oral glucose load" / "75-g OGTT" is a test, not a dose
            found.append(m.group(0).strip())
    for rx in (_EN_WORD_DOSE, _AR_DUAL, _AR_ONE_UNIT):
        found.extend(m.group(0).strip() for m in rx.finditer(norm))
    for rx in (_EN_FREQ, _AR_FREQ):
        for m in rx.finditer(norm):
            if _MONITOR_CONTEXT.search(norm) and not _MEDICINE_CONTEXT.search(norm):
                continue
            found.append(m.group(0).strip())
    if _MEDICINE_CONTEXT.search(norm):
        found.extend(m.group(0).strip() for m in _EN_TIMES.finditer(norm))
    return found


# ---------------------------------------------------------------- medication-advice filter
# The prompt forbids telling the user to start / continue / stop a medicine, but the 7B model still
# wrote "نعم، يمكنك الاستمرار في تناول جلوكوفاج" in the stress test. This second filter removes
# such sentences in code. It only fires when the sentence is about a medicine.
_DRUG_WORDS = re.compile(
    r"\b(?:medicine|medication|drug|tablets?|pills?|insulin|metformin|glucophage|paracetamol|"
    r"acetaminophen|panadol|ibuprofen|brufen|aspirin|nsaids?|antibiotics?|methyldopa|labetalol|"
    r"nifedipine|iron supplements?)\b"
    r"|دواء|الدواء|ادوية|أدوية|علاج|العلاج|حبوب|اقراص|أقراص|انسولين|الأنسولين|الانسولين|ميتفورمين|"
    r"جلوكوفاج|جلوكوفاچ|باراسيتامول|الباراسيتامول|بنادول|إيبوبروفين|ايبوبروفين|بروفين|اسبرين|أسبرين|"
    r"مضاد|المضاد|ميثيل\s*دوبا|لابيتالول",
    re.IGNORECASE,
)
_ADVICE = re.compile(
    r"\b(?:yes,?\s+)?(?:you\s+(?:can|could|may|should)|it\s+is\s+(?:safe|fine|ok|okay)\s+to)\s+"
    r"(?:continue|keep\s+taking|carry\s+on|stop|start|take|use|switch)\b"
    r"|\b(?:continue|keep|stop|start)\s+taking\b|\bdo\s+not\s+stop\b"
    r"|(?:نعم[،,]?\s*)?(?:يمكنك|يمكن\s+لك|يمكن|ممكن|ينفع|تقدري|بإمكانك|باستطاعتك|عليك|يجب\s+عليك)\s+"
    r"(?:الاستمرار|الإستمرار|استمرار|إستمرار|تستمري|تكملي|تكمّلي|مواصله|مواصلة|مواصلت|التوقف|توقفي|إيقاف|ايقاف|تبطلي|"
    r"البدء|تبدئي|تناول|تاخدي|اخذ|أخذ|استخدام)"
    r"|(?<![ء-ي])(?:استمري|كملي|كمّلي|واصلي|اوقفي|أوقفي|وقفي|بطلي|ابدئي)(?![ء-ي])",
    re.IGNORECASE,
)
MEDICATION_ADVICE_NOTICE = {
    "en": ("Whether to start, continue or stop any medicine must be decided with your treating "
           "obstetrician."),
    "ar": "قرار البدء في أي دواء أو الاستمرار فيه أو إيقافه يكون بعد مناقشة طبيب النساء المتابع لحالتك.",
}


def filter_medication_advice(text: str, lang: str) -> FilterResult:
    """Remove sentences that tell the user to start/continue/stop a medicine (pure, no I/O).

    A sentence is removed when it contains an advice phrase AND a medicine word. The notice is put
    once where the first removed sentence was. Same output type as filter_dosage().
    """
    lang = "ar" if lang == "ar" else "en"
    norm = to_ascii_digits(text)
    spans = _sentence_spans(norm)
    kept, matches, added = [], [], False
    for s, e in spans:
        sent = norm[s:e]
        adv = _ADVICE.search(sent)
        if adv and _DRUG_WORDS.search(sent):
            matches.append(adv.group(0).strip())
            if not added:
                kept.append(MEDICATION_ADVICE_NOTICE[lang] + " ")
                added = True
        else:
            kept.append(text[s:e])
    if not matches:
        return FilterResult(text=text, blocked=False, matches=[])
    out = ""
    for piece in kept:
        if out and not out[-1].isspace() and not piece[:1].isspace():
            out += " "
        out += piece
    return FilterResult(text=re.sub(r"[ \t]{2,}", " ", out).strip(), blocked=True, matches=matches)


# ---------------------------------------------------------------- diagnosis filter
# Prompt rule 6 forbids telling the user what she "has", but the 7B model still wrote
# "قد يكون لديك تسمم الحمل أو التهاب الرحم". Such sentences are replaced in code.
_DIAGNOSIS = re.compile(
    r"قد\s+يكون\s+(?:لديك|عندك)|ربما\s+(?:لديك|عندك)|من\s+المحتمل\s+(?:أن|ان)\s+(?:لديك|يكون\s+لديك)"
    r"|(?:انتي|أنتِ|أنت)\s+(?:عندك|لديك)\s+\S+|(?<![ء-ي])(?:لديك|عندك)\s+(?:مرض|التهاب|عدوى|تسمم)"
    r"|\byou\s+(?:may|might|probably|likely|could)\s+(?:have|be\s+suffering\s+from)\b"
    r"|\byou\s+have\s+(?:an?\s+)?(?:infection|disease|condition|pre-?eclampsia|cystitis)\b",
    re.IGNORECASE,
)
DIAGNOSIS_NOTICE = {
    "en": ("These symptoms need to be assessed by a doctor to find the cause; this tool cannot "
           "diagnose."),
    "ar": "الأعراض دي محتاجة كشف عند الطبيب لمعرفة السبب، والأداة دي لا تقدر تشخّص.",
}


def filter_diagnosis(text: str, lang: str) -> FilterResult:
    """Replace sentences that tell the user she has / may have a condition (pure, no I/O)."""
    lang = "ar" if lang == "ar" else "en"
    norm = to_ascii_digits(text)
    kept, matches, added = [], [], False
    for s, e in _sentence_spans(norm):
        m = _DIAGNOSIS.search(norm[s:e])
        if m:
            matches.append(m.group(0).strip())
            if not added:
                kept.append(DIAGNOSIS_NOTICE[lang] + " ")
                added = True
        else:
            kept.append(text[s:e])
    if not matches:
        return FilterResult(text=text, blocked=False, matches=[])
    out = ""
    for piece in kept:
        if out and not out[-1].isspace() and not piece[:1].isspace():
            out += " "
        out += piece
    return FilterResult(text=re.sub(r"[ \t]{2,}", " ", out).strip(), blocked=True, matches=matches)


def filter_dosage(text: str, lang: str) -> FilterResult:
    """Remove dose information from an answer.

    Each sentence that contains a dose is removed; the safe notice (in `lang`) is put once
    where the first removed sentence was. If more than half of the sentences would be removed,
    the whole answer is replaced by the notice.

    Args:
        text: the LLM answer (before the disclaimer is added).
        lang: "ar" or "en", the language of the notice.

    Returns:
        FilterResult(text, blocked, matches).
    """
    lang = "ar" if lang == "ar" else "en"
    notice = get_dosage_notice(lang)  # type: ignore[arg-type]
    norm = to_ascii_digits(text)  # same length as `text`, so spans line up
    spans = _sentence_spans(norm)
    kept: list[str] = []
    matches: list[str] = []
    removed = 0
    notice_added = False
    for s, e in spans:
        hits = find_doses(norm[s:e])
        if hits:
            matches.extend(hits)
            removed += 1
            if not notice_added:
                kept.append(notice + " ")
                notice_added = True
        else:
            kept.append(text[s:e])
    if not matches:
        return FilterResult(text=text, blocked=False, matches=[])
    if removed * 2 > len(spans):
        return FilterResult(text=notice, blocked=True, matches=matches)
    out = ""
    for piece in kept:  # keep one space between sentences (spans do not include it on both sides)
        if out and not out[-1].isspace() and not piece[:1].isspace():
            out += " "
        out += piece
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    return FilterResult(text=out.strip(), blocked=True, matches=matches)
