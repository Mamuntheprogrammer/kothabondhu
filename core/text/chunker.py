"""Split text into engine-sized chunks at natural boundaries."""

from __future__ import annotations

import re

from ..config import CHUNK_MAX_CHARS, CHUNK_MIN_CHARS

_SENTENCE_ENDS = "।!?…"
_CLOSERS = "\"'”’)]»›"
_SPACE = " \t\xa0"
_SPACES = re.compile(r"[ \t\xa0]+")


def collapse(text: str) -> str:
    """Collapse horizontal whitespace but keep paragraph breaks."""
    lines = [_SPACES.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def split_sentences(text: str) -> list[str]:
    """Break on sentence punctuation and newlines, keeping end marks attached."""
    sentences: list[str] = []
    start = 0
    index = 0
    length = len(text)

    while index < length:
        char = text[index]
        if char in "\n\r":
            piece = text[start:index].strip()
            if piece:
                sentences.append(piece)
            index += 1
            while index < length and text[index] in "\n\r":
                index += 1
            while index < length and text[index] in _SPACE:
                index += 1
            start = index
            continue

        if char in _SENTENCE_ENDS or char == ".":
            end = index + 1
            while end < length and text[end] in _SENTENCE_ENDS + "." + _CLOSERS:
                end += 1
            if char == "." and end < length and (text[end].isdigit() or text[end] == "."):
                # Decimal numbers and ellipses are not sentence boundaries.
                index += 1
                continue
            piece = text[start:end].strip()
            if piece:
                sentences.append(piece)
            while end < length and text[end] in _SPACE:
                end += 1
            index = start = end
            continue

        index += 1

    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def _merge_short(sentences: list[str], minimum: int) -> list[str]:
    merged: list[str] = []
    current = ""
    for sentence in sentences:
        current = f"{current} {sentence}" if current else sentence
        if len(current) >= minimum:
            merged.append(current)
            current = ""
    if current:
        if merged:
            merged[-1] = f"{merged[-1]} {current}"
        else:
            merged.append(current)
    return merged


def _hard_wrap(text: str, maximum: int) -> list[str]:
    if len(text) <= maximum:
        return [text]
    pieces: list[str] = []
    remaining = text
    while len(remaining) > maximum:
        window = remaining[:maximum]
        cut = max(window.rfind(" "), window.rfind("\n"))
        if cut <= 0:
            cut = maximum
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)
    return [piece for piece in pieces if piece]


def split(
    text: str,
    language: str = "en",
    max_len: int = CHUNK_MAX_CHARS,
    min_len: int = CHUNK_MIN_CHARS,
) -> list[str]:
    """Full chunking pipeline for a single language segment."""
    text = collapse(text)
    if not text:
        return []
    sentences = split_sentences(text) or [text]
    chunks: list[str] = []
    for piece in _merge_short(sentences, min_len):
        chunks.extend(_hard_wrap(piece, max_len))
    return [chunk for chunk in (piece.strip() for piece in chunks) if chunk] or [text]
