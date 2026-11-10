"""Tests for safety/arabic_text.py.

Author: Abdelrahman Islam.
"""

from safety.arabic_text import detect_language, normalize, to_ascii_digits


def test_digits():
    assert to_ascii_digits("١٤٠/٩٠ و ۱۲") == "140/90 و 12"


def test_normalize():
    assert normalize("أُريدُ الإجابةَ ١٢") == "اريد الاجابه 12"
    assert normalize("حامـــل  مستشفى") == "حامل مستشفي"


def test_detect_language():
    assert detect_language("ينفع آخد Panadol وأنا حامل؟") == "ar"
    assert detect_language("Is paracetamol safe in pregnancy?") == "en"
    assert detect_language("12345") == "en"
