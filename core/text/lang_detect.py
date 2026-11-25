"""Script-based language detection and mixed-text segmentation."""

from __future__ import annotations

BN = "bn"
EN = "en"
DEFAULT = EN
_BENGALI_RANGE = (0x0980, 0x09FF)


def _kind(char: str) -> str | None:
    code = ord(char)
    if _BENGALI_RANGE[0] <= code <= _BENGALI_RANGE[1]:
        return BN
    if "a" <= char <= "z" or "A" <= char <= "Z":
        return EN
    return None


def counts(text: str) -> dict[str, int]:
    tally = {BN: 0, EN: 0}
    for char in text:
        kind = _kind(char)
        if kind is not None:
            tally[kind] += 1
    return tally


def detect_language(text: str, default: str = DEFAULT) -> str:
    tally = counts(text)
    if not tally[BN] and not tally[EN]:
        return default
    return BN if tally[BN] > tally[EN] else EN


def is_mixed(text: str) -> bool:
    tally = counts(text)
    return tally[BN] > 0 and tally[EN] > 0


def split_segments(text: str) -> list[tuple[str, str]]:
    """Split into ordered ``(language, text)`` runs, keeping neutral chars attached."""
    segments: list[tuple[str, str]] = []
    current: list[str] = []
    current_lang: str | None = None
    pending: list[str] = []

    def flush() -> None:
        nonlocal current, current_lang
        piece = "".join(current).strip()
        if piece:
            segments.append((current_lang or DEFAULT, piece))
        current, current_lang = [], None

    for char in text:
        kind = _kind(char)
        if kind is None:
            (current if current_lang else pending).append(char)
            continue
        if current_lang is None:
            current_lang = kind
            current.extend(pending)
            pending.clear()
        elif kind != current_lang:
            flush()
            current_lang = kind
            current.extend(pending)
            pending.clear()
        current.append(char)

    flush()
    return segments
