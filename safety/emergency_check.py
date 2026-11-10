"""Preliminary emergency check (Phase 1 version). Runs BEFORE retrieval, without the LLM.

Purpose
    Some messages describe danger signs in pregnancy or after birth. Whatever the guidelines say,
    the user must first be told to go to emergency care. This module looks for those signs with
    deterministic rules (no model), in Arabic (incl. Egyptian dialect) and English, after
    normalization, and ignores negated mentions ("مفيش نزيف", "no bleeding").

    The symptom list follows the team master plan §3.3: bleeding; leaking fluid / waters breaking;
    reduced or absent fetal movement; severe headache or visual disturbance; severe abdominal pain;
    seizure or fainting; shortness of breath or chest pain; high fever (especially after birth);
    contractions before 37 weeks; sudden swelling of the face or hands.

    This is a deliberately simple first version, biased towards caution (a false alarm is better
    than a missed emergency). The full emergency interceptor (blood-pressure extraction,
    vitals + symptom rules, a 50+ phrase recall test set) is Phase 2 (`emergency_interceptor.py`,
    Member 4). The rules are keyword patterns written by the developer, not clinical thresholds,
    and have not been reviewed by a clinician.

Public interface: check_emergency(text) -> EmergencyResult(is_emergency, categories, matches)
Pure function: no I/O.

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from safety.arabic_text import normalize

_AR = "ء-ي"
_B = rf"(?<![{_AR}a-z])(?:و|ف)?"   # start of word, allowing the prefixes و / ف ("وسخونيه")
_E = rf"(?![{_AR}a-z])"    # end of word

# Negation words that cancel a symptom mentioned right after them ("مفيش نزيف", "no bleeding").
_NEG = re.compile(
    rf"{_B}(?:مفيش|مافيش|ما فيش|مش|مو|ليس|لا يوجد|لا|بدون|من غير|مابقاش)\s+(?:\S+\s+)?$"
    r"|\b(?:no|not|without|never|don't have|do not have|denies|no sign of)\s+(?:\S+\s+)?$",
    re.IGNORECASE)

# (category, pattern, negatable). Arabic patterns are written in NORMALIZED form
# (ة->ه, ى->ي, أ/إ/آ->ا, no diacritics), because the text is normalized first.
_RULES: list[tuple[str, str, bool]] = [
    # bleeding
    ("bleeding", rf"{_B}(?:نزيف|بنزف|بتنزف|نزف|نازف)", True),
    ("bleeding", rf"{_B}(?:دم|الدم)\s+(?:نازل|بينزل|كتير|نزل)|{_B}نزل\s+(?:مني\s+)?دم{_E}", True),
    ("bleeding", r"\bbleeding\b|\bblood\s+clots?\b", True),
    # the medical term alone is usually a topic ("prevent postpartum haemorrhage"); it counts only
    # when the person speaks about herself
    ("bleeding", r"\b(?:i|i'm|my|me)\b.{0,40}\b(?:haemorrhag\w*|hemorrhag\w*|blood\s+loss)\b", True),
    # leaking fluid / waters
    ("fluid_leak", rf"{_B}(?:تسريب|بتسرب|بيسرب|تسرب)", True),
    ("fluid_leak", rf"{_B}(?:نزلت|نازله|بتنزل|بتنقط|نزل)\s+(?:مني\s+)?(?:مي|ميه|مايه|مياه)|{_B}(?:الميه|المايه|المياه)\s+(?:نزلت|بتنزل|نازله)|كيس\s+(?:الميه|المايه|المياه)\s+(?:اتفتح|فتح|انفجر)", True),
    ("fluid_leak", r"\b(?:waters?|water)\s+(?:has\s+|have\s+)?(?:broke|broken|breaking)\b|\bleak\w*\s+(?:of\s+)?fluid\b|\bgush\s+of\s+fluid\b|\bfluid\s+leak", True),
    # reduced fetal movement (the negation is part of the symptom, so not negatable)
    ("fetal_movement", rf"(?:البيبي|الجنين|البيبى|ابني|بنتي|العيل)?\s*(?:مش|مبقاش|ما بقاش|بطل|بطلت)\s*(?:بي|بت)?(?:تحرك|يتحرك|بيتحرك|بتتحرك|يرفس|بيرفس|بيلعب)", False),
    ("fetal_movement", rf"{_B}(?:مبيتحركش|ما بيتحركش|مابيتحركش|مبيرفسش|مش حاسه بحركته|مش حاسه بحركه|مش حاسه بيه)", False),
    ("fetal_movement", rf"(?:حركه|حركته|حركة|الحركه)\s+(?:الجنين\s+|البيبي\s+)?(?:قلت|قليله|خفت|خفيفه|وقفت|بقت قليله|اقل)", False),
    ("fetal_movement", r"\b(?:baby|fetus|foetus)\s+(?:is\s+|has\s+)?(?:not|stopped|isn't|hasn't been)\s+(?:moving|kicking)\b|\b(?:reduced|decreased|less|fewer|no)\s+(?:fetal\s+|foetal\s+|baby\s+)?(?:movements?|kicks?)\b", False),
    # severe headache / visual disturbance
    ("headache_vision", rf"{_B}صداع\s+(?:جامد|شديد|فظيع|مش بيروح|مبيروحش|مستمر|قوي)", True),
    ("headache_vision", rf"{_B}(?:زغلله|بتزغلل|بيزغلل|زغلل|غباش|مش شايفه كويس|بشوف نقط|بشوف نور|نظري ضعف)", True),
    ("headache_vision", r"\bsevere\s+headache\b|\bheadache\s+(?:that\s+)?(?:won't|will not|doesn't)\s+go\b|\bblurr(?:ed|y)\s+vision\b|\bflashing\s+lights?\b|\bspots\s+(?:in|before)\s+(?:my\s+)?eyes\b|\bvision\s+(?:problems?|changes?)\b", True),
    # severe abdominal pain
    ("abdominal_pain", rf"(?:بطني|البطن|بطن|معدتي)\s*(?:\S+\s+){{0,2}}(?:وجعاني|بتوجعني|بيوجعني|وجع|الم|مغص)\s*(?:\S+\s+){{0,1}}(?:خالص|جامد|شديد|اوي|فظيع|مش طبيعي)", True),
    ("abdominal_pain", rf"(?:وجع|الم|مغص)\s+(?:جامد|شديد|فظيع)\s+(?:في\s+)?(?:بطني|البطن|بطن)", True),
    ("abdominal_pain", r"\bsevere\s+(?:abdominal|stomach|belly|tummy)\s+pain\b|\b(?:abdominal|stomach|belly)\s+pain\s+(?:is\s+)?(?:severe|very bad|unbearable)\b|\bupper\s+(?:right\s+)?abdominal\s+pain\b", True),
    # seizure / fainting
    ("seizure_faint", rf"{_B}(?:تشنج|تشنجات|اتشنجت|بتشنج|اغمي عليا|اغمى عليا|اغمي علي|اغمى علي|اغمي|اغماء|فقدت الوعي|وقعت من طولي)", True),
    ("seizure_faint", r"\b(?:seizures?|convulsions?|convulsing|fainted|fainting|passed out|unconscious|lost consciousness)\b", True),
    # breathing / chest
    ("breathing_chest", rf"{_B}(?:ب?ضيق\s+(?:في\s+)?(?:نفس|النفس|التنفس)|نفسي\s+(?:مقطوع|بيتقطع)|مش قادره\s+(?:اتنفس|اخد نفسي)|وجع\s+(?:في\s+)?صدري|الم\s+(?:في\s+)?صدري)", True),
    ("breathing_chest", r"\bshort(?:ness)?\s+of\s+breath\b|\bcan(?:'|no)?t\s+breathe\b|\bdifficulty\s+breathing\b|\bchest\s+pain\b", True),
    # high fever (especially after birth)
    ("fever", rf"{_B}(?:سخونيه|سخونه|حمي|حراره|سخن)(?:\s+\S+){{0,4}}\s+(?:عاليه|جامده|شديده|رعشه|بعد الولاده|بعد ما ولدت|39|40|41)", True),
    ("fever", rf"{_B}(?:رعشه|قشعريره)(?:\s+\S+){{0,4}}\s+(?:بعد الولاده|بعد ما ولدت)|(?:بعد الولاده|بعد ما ولدت)(?:\s+\S+){{0,5}}\s+(?:سخونيه|سخونه|حمي|حراره)", True),
    ("fever", r"\bhigh\s+(?:fever|temperature)\b|\bfever\b.{0,40}\b(?:after\s+(?:giving\s+)?birth|postpartum|after\s+delivery|chills|shivering)\b|\b(?:after\s+(?:giving\s+)?birth|postpartum)\b.{0,40}\bfever\b", True),
    # contractions before 37 weeks / preterm labour
    ("preterm_labour", r"\bpre-?term\s+labou?r\b|\bcontractions?\b.{0,40}\b(?:[12]\d|3[0-6])\s*weeks?\b|\b(?:[12]\d|3[0-6])\s*weeks?\b.{0,40}\bcontractions?\b", True),
    ("preterm_labour", rf"(?:طلق|انقباضات|تقلصات)(?:\s+\S+){{0,5}}\s+(?:بدري|في الشهر\s+(?:السابع|السادس|الخامس|السابع)|الاسبوع\s+(?:[12]\d|3[0-6]))", True),
    # sudden swelling of face / hands
    ("swelling", rf"(?:وشي|وجهي|ايدي|ايديا|صوابعي|عينيا)(?:\s+\S+){{0,3}}\s+(?:وارم|وارمه|وارمين|ورم|منفوخ|منفوخه|منفوخين|متورم|متورمه|متورمين)", True),
    ("swelling", rf"(?:ورم|تورم|انتفاخ)\s+(?:مفاجئ\s+)?(?:في\s+)?(?:وشي|وجهي|الوش|الوجه|ايدي|الايدين)", True),
    ("swelling", r"\b(?:face|hands?|fingers)\b.{0,25}\b(?:swollen|swelling|puffy)\b|\b(?:swollen|swelling|puffy)\b.{0,25}\b(?:face|hands?|fingers)\b", True),
]
_COMPILED = [(cat, re.compile(p, re.IGNORECASE), neg) for cat, p, neg in _RULES]

# A pure definition question with no sign that the person describes herself ("What is postpartum
# haemorrhage?", "يعني ايه نزيف ما بعد الولاده") is information, not an emergency report.
_DEFINITION_Q = re.compile(
    r"^\s*(?:what\s+(?:is|are|does|causes)|define|explain|tell me about|يعني\s+ايه|ما\s+هو|ما\s+هي|ماهو|ماهي|ايه\s+هو|ايه\s+هي|ايه\s+اعراض|ايه\s+اسباب|ما\s+اعراض|ما\s+اسباب)(?![ء-يa-z])",
    re.IGNORECASE)
_FIRST_PERSON = re.compile(
    rf"{_B}(?:عندي|انا|بنزف|حاسه|حاسس|ليا|معايا|بطني|راسي|عيني|وشي|ايدي|ولدت|بولد){_E}"
    r"|(?<![a-z'])(?:i|i'm|i've|me|my|mine)(?![a-z'])", re.IGNORECASE)


@dataclass(frozen=True)
class EmergencyResult:
    """is_emergency: at least one danger sign found; categories: which ones; matches: the text found."""

    is_emergency: bool
    categories: list[str] = field(default_factory=list)
    matches: list[str] = field(default_factory=list)


def check_emergency(text: str) -> EmergencyResult:
    """Look for danger signs in a user message (Arabic or English). No model, no I/O."""
    norm = normalize(text).lower()
    if _DEFINITION_Q.search(norm) and not _FIRST_PERSON.search(norm):
        return EmergencyResult(is_emergency=False)
    cats: list[str] = []
    found: list[str] = []
    for cat, rx, negatable in _COMPILED:
        for m in rx.finditer(norm):
            before = norm[max(0, m.start() - 25): m.start()]
            if negatable and _NEG.search(before):
                continue
            if cat not in cats:
                cats.append(cat)
            found.append(m.group(0).strip())
    return EmergencyResult(is_emergency=bool(cats), categories=cats, matches=found)
