"""Expand Bangla digits, dates and symbols into words the model can pronounce."""

from __future__ import annotations

import re
import unicodedata

_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_SPACES = re.compile(r"[ \t\xa0]+")

# Bengali number words do not compose 21 as "ten one", so 0-99 is explicit.
_UNDER_100 = [
    "শূন্য", "এক", "দুই", "তিন", "চার", "পাঁচ", "ছয়", "সাত", "আট", "নয়",
    "দশ", "এগারো", "বারো", "তেরো", "চৌদ্দ", "পনেরো", "ষোলো", "সতেরো", "আঠারো", "উনিশ",
    "বিশ", "একুশ", "বাইশ", "তেইশ", "চব্বিশ", "পঁচিশ", "ছাব্বিশ", "সাতাশ", "আটাশ", "ঊনত্রিশ",
    "ত্রিশ", "একত্রিশ", "বত্রিশ", "তেত্রিশ", "চৌত্রিশ", "পঁয়ত্রিশ", "ছত্রিশ", "সাঁইত্রিশ", "আটত্রিশ", "ঊনচল্লিশ",
    "চল্লিশ", "একচল্লিশ", "বিয়াল্লিশ", "তেতাল্লিশ", "চুয়াল্লিশ", "পঁয়তাল্লিশ", "ছেচল্লিশ", "সাতচল্লিশ", "আটচল্লিশ", "ঊনপঞ্চাশ",
    "পঞ্চাশ", "একান্ন", "বায়ান্ন", "তিপ্পান্ন", "চুয়ান্ন", "পঞ্চান্ন", "ছাপ্পান্ন", "সাতান্ন", "আটান্ন", "ঊনষাট",
    "ষাট", "একষট্টি", "বাষট্টি", "তেষট্টি", "চৌষট্টি", "পঁয়ষট্টি", "ছেষট্টি", "সাতষট্টি", "আটষট্টি", "ঊনসত্তর",
    "সত্তর", "একাত্তর", "বাহাত্তর", "তিয়াত্তর", "চুয়াত্তর", "পঁচাত্তর", "ছিয়াত্তর", "সাতাত্তর", "আটাত্তর", "ঊনআশি",
    "আশি", "একাশি", "বিরাশি", "তিরাশি", "চুরাশি", "পঁচাশি", "ছিয়াশি", "সাতাশি", "আটাশি", "ঊননব্বই",
    "নব্বই", "একানব্বই", "বিরানব্বই", "তিরানব্বই", "চুরানব্বই", "পঁচানব্বই", "ছিয়ানব্বই", "সাতানব্বই", "আটানব্বই", "নিরানব্বই",
]

_HUNDREDS = [
    "", "একশ", "দুইশ", "তিনশ", "চারশ", "পাঁচশ", "ছয়শ", "সাতশ", "আটশ", "নয়শ",
]
_ORDINALS = {
    1: "প্রথম", 2: "দ্বিতীয়", 3: "তৃতীয়", 4: "চতুর্থ", 5: "পঞ্চম",
    6: "ষষ্ঠ", 7: "সপ্তম", 8: "অষ্টম", 9: "নবম", 10: "দশম",
}
_MONTHS = [
    "জানুয়ারি", "ফেব্রুয়ারি", "মার্চ", "এপ্রিল", "মে", "জুন",
    "জুলাই", "আগস্ট", "সেপ্টেম্বর", "অক্টোবর", "নভেম্বর", "ডিসেম্বর",
]
_ABBREVIATIONS = {
    "ডাঃ": "ডাক্তার", "ডঃ": "ডাক্তার", "ইঞ্জি": "ইঞ্জিনিয়ার",
    "মোঃ": "মোহাম্মদ", "অধ্যা": "অধ্যাপক", "গ্রা": "গ্রাম",
    "কিঃ": "কিলোমিটার", "মিঃ": "মিটার", "সেঃ": "সেন্টিমিটার",
}
_SYMBOLS = {
    "%": "শতাংশ", "৳": "টাকা", "$": "ডলার", "€": "ইউরো",
    "&": "এবং", "+": "যোগ", "×": "গুণ", "÷": "ভাগ",
    "=": "সমান", "°": "ডিগ্রি", "#": "নম্বর", "@": "অ্যাট",
    "<": "কম", ">": "বেশি", "©": "কপিরাইট",
    "®": "নিবন্ধিত ট্রেডমার্ক", "™": "ট্রেডমার্ক",
}

_INTEGER = re.compile(r"(?<![A-Za-z0-9])\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9])")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_CURRENCY = re.compile(r"৳\s*(\d[\d,]*(?:\.\d+)?)")
_DATE_DMY = re.compile(r"(?<!\d)(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})(?!\d)")
_DATE_YMD = re.compile(r"(?<!\d)(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})(?!\d)")
_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
_ORDINAL = re.compile(r"(?<!\d)(\d{1,2})(ম|য়|ই|তম|র্থ|লা)(?![\dA-Za-z])")
_ABBREV = re.compile(
    r"(?<![\u0980-\u09FF])(" + "|".join(map(re.escape, _ABBREVIATIONS)) + r")(?![\u0980-\u09FF])"
)
_BENGALI = re.compile(r"[\u0980-\u09FF]")


def under_thousand(number: int) -> str:
    """Spell 0-999."""
    if number < 100:
        return _UNDER_100[number]
    hundreds, rest = divmod(number, 100)
    words = [_HUNDREDS[hundreds]]
    if rest:
        words.append(_UNDER_100[rest])
    return " ".join(word for word in words if word)


def number_to_words(number: int) -> str:
    """Spell an integer using হাজার / লাখ / কোটি grouping."""
    number = int(number)
    if number < 0:
        return "ঋণ " + number_to_words(-number)
    if number < 1000:
        return under_thousand(number)

    groups: list[str] = []
    for divisor, name in ((10**7, "কোটি"), (10**5, "লাখ"), (1000, "হাজার")):
        amount, number = divmod(number, divisor)
        if amount:
            head = number_to_words(amount) if amount >= 1000 else under_thousand(amount)
            groups.append(f"{head} {name}")
    if number:
        groups.append(under_thousand(number))
    return " ".join(groups)


def decimal_to_words(token: str) -> str:
    """``12.34`` -> ``বারো দশমিক তিন চার``."""
    whole, _, fraction = token.partition(".")
    whole = whole.replace(",", "") or "0"
    words = [number_to_words(int(whole))] if int(whole) else []
    if fraction:
        words.append("দশমিক")
        words.extend(_UNDER_100[int(digit)] for digit in fraction if digit.isdigit())
    return " ".join(words) or _UNDER_100[0]


def _date(day: str, month: str, year: str) -> str | None:
    day, month, year = int(day), int(month), int(year)
    if not (1 <= month <= 12 and 1 <= day <= 31 and 1000 <= year <= 2999):
        return None
    return f"{number_to_words(day)} {_MONTHS[month - 1]} {number_to_words(year)}"


def _ordinal(match: re.Match) -> str:
    number = int(match.group(1))
    special = _ORDINALS.get(number)
    if special:
        return special
    suffix = match.group(2)
    if suffix in ("ম", "য়", "ই", "তম"):
        suffix = "তম"
    return f"{number_to_words(number)}{suffix}"


def _strip_unsupported(text: str) -> str:
    """Drop symbols and format marks the model has no tokens for."""
    kept: list[str] = []
    for char in text:
        if char.isspace():
            kept.append("\n" if char in "\n\r" else " ")
        elif unicodedata.category(char)[0] not in ("S", "C"):
            kept.append(char)
    return "".join(kept)


def _collapse(text: str) -> str:
    return "\n".join(
        line for line in (_SPACES.sub(" ", part).strip() for part in text.split("\n")) if line
    ).strip()


def normalize(text: str) -> str:
    """Expand everything the Bangla model cannot pronounce."""
    if not text:
        return ""

    text = text.translate(_DIGITS)
    text = _DATE_YMD.sub(
        lambda match: _date(match.group(3), match.group(2), match.group(1)) or match.group(0), text
    )
    text = _DATE_DMY.sub(
        lambda match: _date(match.group(1), match.group(2), match.group(3)) or match.group(0), text
    )
    text = _TIME.sub(
        lambda match: f"{number_to_words(int(match.group(1)))}টা "
        f"{number_to_words(int(match.group(2)))} মিনিট",
        text,
    )
    text = _ORDINAL.sub(_ordinal, text)
    text = _PERCENT.sub(lambda match: f"{decimal_to_words(match.group(1))} শতাংশ", text)
    text = _CURRENCY.sub(lambda match: f"{decimal_to_words(match.group(1))} টাকা", text)
    text = _ABBREV.sub(lambda match: _ABBREVIATIONS[match.group(1)], text)
    text = _INTEGER.sub(lambda match: decimal_to_words(match.group(0)), text)

    for symbol, spoken in _SYMBOLS.items():
        if symbol in text:
            text = text.replace(symbol, f" {spoken} ")

    return _collapse(_strip_unsupported(text))


def contains_bengali(text: str) -> bool:
    return bool(_BENGALI.search(text))
