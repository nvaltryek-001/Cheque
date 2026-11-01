"""Indian-system amount parsing: '12,34,567/-' <-> 'Twelve Lakh Thirty Four Thousand Five Hundred Sixty Seven'."""
from __future__ import annotations

import re

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000,
           "lacs": 100_000, "crore": 10_000_000, "crores": 10_000_000}
_FILLER = {"and", "only", "rupees", "rupee", "rs", "inr", "paise", "-"}


def _fuzzy(word: str) -> str | None:
    """Map a slightly misspelled OCR token to a number word (edit distance <= 1, len >= 4)."""
    if word in _UNITS or word in _SCALES or word in _FILLER:
        return word
    if len(word) < 4:
        return None
    for cand in list(_UNITS) + list(_SCALES):
        if abs(len(cand) - len(word)) > 1:
            continue
        # cheap Levenshtein <= 1
        if len(cand) == len(word):
            if sum(a != b for a, b in zip(cand, word)) <= 1:
                return cand
        else:
            s, l = (word, cand) if len(word) < len(cand) else (cand, word)
            for i in range(len(l)):
                if l[:i] + l[i + 1:] == s:
                    return cand
    return None


def words_to_number(text: str) -> int | None:
    if not text:
        return None
    tokens = re.findall(r"[a-zA-Z]+", text.lower())
    total, current, seen = 0, 0, False
    for raw in tokens:
        tok = _fuzzy(raw)
        if tok is None or tok in _FILLER:
            if tok is None and seen:
                # unknown token in the middle of a number phrase: ignore but keep going
                continue
            continue
        seen = True
        if tok in _UNITS:
            current += _UNITS[tok]
        elif tok == "hundred":
            current = max(current, 1) * 100
        else:
            scale = _SCALES[tok]
            total += max(current, 1) * scale
            current = 0
    value = total + current
    return value if seen and value > 0 else None


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven",
         "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two(n: int) -> str:
    return _ONES[n] if n < 20 else (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else ""))


def number_to_words(n: int) -> str:
    if n <= 0:
        return "Zero"
    parts = []
    for div, name in ((10_000_000, "Crore"), (100_000, "Lakh"), (1_000, "Thousand"), (100, "Hundred")):
        if n >= div:
            parts.append(f"{number_to_words(n // div) if div == 10_000_000 else _two(n // div)} {name}")
            n %= div
    if n:
        parts.append(_two(n))
    return " ".join(parts)


def parse_figures(text: str) -> int | None:
    """'₹ 23,50,000/-' -> 2350000. Ignores paise after a decimal point."""
    if not text:
        return None
    t = text.replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1")
    m = re.search(r"(\d[\d,\s]*\d|\d)(?:\.\d{1,2})?\s*/?-?", t)
    if not m:
        return None
    digits = re.sub(r"[^\d]", "", m.group(1))
    if not digits or len(digits) > 12:
        return None
    return int(digits)


def format_indian(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail])
