"""Arabic text helpers: normalization, digit conversion and language detection.

Purpose
    Arabic text can be written many ways (أ/إ/آ/ا, ى/ي, ة/ه, diacritics, Arabic-Indic digits).
    These pure functions make text comparable for regex rules (dosage filter now, emergency
    interceptor and perception in Phase 2) and decide whether a question is Arabic or English.

Public interface (stable, used by Phase 2):
    normalize(text) -> str
    to_ascii_digits(text) -> str
    detect_language(text) -> "ar" | "en"

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import re
from typing import Literal

# Arabic-Indic (U+0660..0669) and Persian/Urdu (U+06F0..06F9) digits -> ASCII.
# The Arabic decimal separator (U+066B) becomes "." and the Arabic comma (U+060C) stays.
_DIGIT_MAP = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_DIGIT_MAP.update({ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})
_DIGIT_MAP[0x066B] = "."

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
_TATWEEL = "ـ"
_ARABIC_LETTER = re.compile(r"[ء-يٱ-ۓ]")
_LATIN_LETTER = re.compile(r"[A-Za-z]")


def to_ascii_digits(text: str) -> str:
    """Convert Arabic-Indic and Persian digits to ASCII digits. Length is preserved (1 char -> 1 char)."""
    return text.translate(_DIGIT_MAP)


def normalize(text: str) -> str:
    """Normalize Arabic text for matching.

    Removes diacritics and tatweel, unifies alef forms (أ إ آ ٱ -> ا), alef maqsura (ى -> ي),
    ta marbuta (ة -> ه) and hamza carriers (ؤ -> و, ئ -> ي), converts digits to ASCII and
    collapses whitespace. English text passes through unchanged except for whitespace.

    >>> normalize("أُريدُ الإجابةَ ١٢")
    'اريد الاجابه 12'
    """
    text = to_ascii_digits(text)
    text = _DIACRITICS.sub("", text).replace(_TATWEEL, "")
    text = re.sub("[أإآٱ]", "ا", text)
    text = text.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    return re.sub(r"\s+", " ", text).strip()


def arabic_ratio(text: str) -> float:
    """Share of letters that are Arabic script (0.0 when the text has no letters)."""
    ar = len(_ARABIC_LETTER.findall(text))
    lat = len(_LATIN_LETTER.findall(text))
    return ar / (ar + lat) if (ar + lat) else 0.0


def detect_language(text: str, threshold: float = 0.3) -> Literal["ar", "en"]:
    """Return "ar" when at least `threshold` of the letters are Arabic script, else "en".

    A low threshold is used because Arabic questions often contain English drug names,
    e.g. "ينفع آخد Panadol وأنا حامل؟" is still an Arabic question.
    """
    return "ar" if arabic_ratio(text) >= threshold else "en"
