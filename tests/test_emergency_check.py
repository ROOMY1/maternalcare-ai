"""Tests for safety/emergency_check.py (preliminary Phase 1 emergency check).

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import pytest

from safety.emergency_check import check_emergency

EMERGENCY = [
    # the six emergency questions that failed in the manual UI test, plus the swelling one
    "عندي صداع جامد مش بيروح وعيني بتزغلل",
    "البيبي مش بيتحرك زي الاول من امبارح",
    "بطني مكركبه ووجعاني خالص و حاسه في تسريب",
    "بعد الولاده عندي نزيف كتير اوي",
    "عندي سخونيه ورعشه بعد الولاده بيومين",
    "My water broke at 34 weeks, what should I do?",
    "رجلي ووشي وارمين اوي ده طبيعي",
    # more phrasings
    "نزل مني دم وانا في الشهر التالت",
    "الميه نزلت مني",
    "حركة البيبي قلت",
    "اغمى عليا النهارده",
    "مش قادره اتنفس",
    "حاسه بضيق في النفس",
    "عندي طلق وانا في الاسبوع 33",
    "عيني بتزغلل",
    "The baby is not moving as much since yesterday",
    "I am bleeding heavily after delivery",
    "I have a fever and chills after giving birth",
    "I fainted this morning",
    "I have a severe headache and blurred vision at 32 weeks",
    "What is happening, I am bleeding a lot",
    "يعني ايه انا عندي نزيف",
    "عندي صداع جامد",  # Arabic-Indic digits and diacritics are normalized first
    "I think I am having a postpartum haemorrhage",
]

NOT_EMERGENCY = [
    "مفيش نزيف بس عندي سؤال عن الحديد",       # negated
    "مش عندي صداع بس عايزه اسأل عن الكالسيوم",  # negated
    "I have no bleeding, is spotting normal?",   # negated
    "ينفع اكل تونه",
    "What foods should I avoid?",
    "ايه اعراض تسمم الحمل",
    "How is gestational diabetes diagnosed?",
    "لبني قليل اوي اعمل ايه",
    "رجلي وارمه شويه",                           # legs only, not face/hands
    "ينفع اخد بروفين وانا في الشهر السادس",
    "What is postpartum haemorrhage?",            # definition question, no first person
    "يعني ايه نزيف ما بعد الولاده؟ وهو خطير؟",
    "هما بيسمعوا نبض البيبي ازاي وانا بولد",
    "When should breastfeeding start after birth?",
    "Which medicine is recommended to prevent postpartum haemorrhage?",  # false alarm found by Ragas run
]


@pytest.mark.parametrize("text", EMERGENCY)
def test_emergency_detected(text):
    r = check_emergency(text)
    assert r.is_emergency and r.categories, text


@pytest.mark.parametrize("text", NOT_EMERGENCY)
def test_no_false_alarm(text):
    r = check_emergency(text)
    assert not r.is_emergency, f"{text} -> {r.categories} {r.matches}"


def test_categories_are_reported():
    r = check_emergency("بعد الولاده عندي نزيف كتير وسخونيه عاليه")
    assert "bleeding" in r.categories and "fever" in r.categories
