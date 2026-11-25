"""Urdu / Arabic / Persian / Hebrew text for PDF: join the letters and put each line in visual order.

A PDF draws glyphs from left to right and does not join letters, so before drawing
  shape(text)  replaces each Arabic letter by its isolated / initial / medial / final form (Unicode presentation forms),
  visual(line) reverses the right-to-left parts of one line (numbers and Latin words keep their direction).
Both work without extra packages, for Naskh fonts. With the uharfbuzz package, shaping.py joins the letters of a TrueType
font by the font's own tables instead (Nastaliq needs that) and takes only the order of the parts, runs(line), from here.
"""
from __future__ import annotations

import unicodedata
from functools import lru_cache
from itertools import groupby
from typing import Callable

LAM = "ل"
TATWEEL = "ـ"
MIRROR = {"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{", "<": ">", ">": "<", "«": "»", "»": "«"}
# letters without presentation forms of their own: drawn with the forms of the base letter, then the mark
_SPLIT = {"ۂ": "ۂ"}


@lru_cache(maxsize=1)
def _forms() -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """({letter: {"initial": …, "medial": …, "final": …, "isolated": …}}, the same for lam-alef pairs)."""
    letters: dict[str, dict[str, str]] = {}
    pairs: dict[str, dict[str, str]] = {}
    for code in (*range(0xFB50, 0xFBFF + 1), *range(0xFE80, 0xFEFC + 1)):
        parts = unicodedata.decomposition(chr(code)).split()
        if not parts or not parts[0].startswith("<"):
            continue
        base = "".join(chr(int(part, 16)) for part in parts[1:])
        target = letters if len(base) == 1 else pairs if len(base) == 2 and base[0] == LAM and code >= 0xFEF5 else None
        if target is not None:
            target.setdefault(base, {}).setdefault(parts[0].strip("<>"), chr(code))
    # in Urdu text U+0649 / U+06CC: the Farsi yeh has all four forms, alef maksura only two; leave each as it is
    return letters, pairs


def has_rtl(text: str) -> bool:
    return any("֐" <= ch <= "ࣿ" or "יִ" <= ch <= "ﻼ" for ch in text)


def shape(text: str, covers: Callable[[str], bool] = lambda _ch: True) -> str:
    """Join Arabic letters; `covers` says whether the font has a character."""
    if not has_rtl(text):
        return text
    for whole, parts in _SPLIT.items():
        text = text.replace(whole, parts)
    letters, pairs = _forms()
    chars = list(text)
    marks = [unicodedata.category(ch) == "Mn" for ch in chars]

    def neighbour(index: int, step: int) -> str:
        index += step
        while 0 <= index < len(chars) and marks[index]:
            index += step
        return chars[index] if 0 <= index < len(chars) else ""

    def joins_next(ch: str) -> bool:
        return ch == TATWEEL or "initial" in letters.get(ch, {})

    def joins_previous(ch: str) -> bool:
        return ch == TATWEEL or "final" in letters.get(ch, {})

    out: list[str] = []
    skip = -1
    for index, ch in enumerate(chars):
        if index == skip:
            continue
        own = letters.get(ch)
        if not own:
            if ch not in "‌‍":                      # joiners have done their work
                out.append(ch)
            continue
        before = joins_next(neighbour(index, -1))
        after = neighbour(index, 1)
        if ch == LAM and LAM + after in pairs:
            form = pairs[LAM + after].get("final" if before else "isolated", "")
            if form and covers(form):
                out.append(form)
                skip = next(i for i in range(index + 1, len(chars)) if not marks[i])
                out.extend(chars[index + 1:skip])
                for i in range(index + 1, skip):
                    chars[i] = "\u200d"                        # its marks are written already
                continue
        if joins_next(ch) and joins_previous(after):
            name = "medial" if before else "initial"
        else:
            name = "final" if before else "isolated"
        form = own.get(name) or ch
        out.append(form if covers(form) else ch)
    return "".join(out)


def _kind(ch: str) -> str:
    kind = unicodedata.bidirectional(ch)
    if kind in ("R", "AL"):
        return "R"
    if kind == "L":
        return "L"
    return "N" if kind in ("EN", "AN") else "M" if kind == "NSM" else "O"


def _levels(line: str) -> list[int]:
    """The level of each character (odd: right to left), by the Unicode bidirectional algorithm without embeddings."""
    kinds = [_kind(ch) for ch in line]
    base = next((kind for kind in kinds if kind in "RL"), "L")
    for i, kind in enumerate(kinds):                            # marks belong to the letter before them
        if kind == "M":
            kinds[i] = kinds[i - 1] if i else base
    for i, kind in enumerate(kinds):                            # 12,500 / 10.5 / 01/02/2026: one number
        if kind == "O" and 0 < i < len(kinds) - 1 and line[i] in ".,:/-" and kinds[i - 1] == "N" and kinds[i + 1] == "N":
            kinds[i] = "N"
    strong = base
    for i, kind in enumerate(kinds):                            # a number after Latin text goes with that text
        if kind in "RL":
            strong = kind
        elif kind == "N" and strong == "L":
            kinds[i] = "L"
    i = 0
    while i < len(kinds):
        if kinds[i] != "O":
            i += 1
            continue
        end = i
        while end < len(kinds) and kinds[end] == "O":
            end += 1
        left = "R" if i and kinds[i - 1] in "RN" else "L" if i else base
        right = "R" if end < len(kinds) and kinds[end] in "RN" else "L" if end < len(kinds) else base
        for j in range(i, end):
            kinds[j] = left if left == right else base
        i = end
    return [(0 if kind == "L" else 1 if kind == "R" else 2) if base == "L" else (1 if kind == "R" else 2) for kind in kinds]


def _ordered(items: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Put (text, level) pieces in the order they are drawn."""
    for level in range(max((item[1] for item in items), default=0), 0, -1):
        i = 0
        while i < len(items):
            if items[i][1] < level:
                i += 1
                continue
            end = i
            while end < len(items) and items[end][1] >= level:
                end += 1
            items[i:end] = items[i:end][::-1]
            i = end
    return items


def visual(line: str) -> str:
    """One line in the order it is drawn, left to right."""
    if not has_rtl(line):
        return line
    return "".join(ch for ch, _ in _ordered([(MIRROR.get(ch, ch) if level % 2 else ch, level) for ch, level in zip(line, _levels(line))]))


def runs(line: str) -> list[tuple[str, bool]]:
    """One line as [(text in reading order, right to left?)], the parts in the order they are drawn, left to right."""
    if not has_rtl(line):
        return [(line, False)] if line else []
    items = [("".join(ch for ch, _ in group), level) for level, group in groupby(zip(line, _levels(line)), key=lambda pair: pair[1])]
    return [(text, level % 2 == 1) for text, level in _ordered(items)]
