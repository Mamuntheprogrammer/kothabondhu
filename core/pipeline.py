"""Text planning: turn a request into ordered ``(language, chunk)`` jobs."""

from __future__ import annotations

from dataclasses import dataclass

from .text import chunker, lang_detect, normalize_bn


@dataclass
class Request:
    """Everything one press of Speak needs."""

    text: str
    language: str = "auto"  # "auto" | "en" | "bn"
    speed: float = 1.0
    voice_en: str | None = None
    voice_bn: str | None = None
    loudness_match: bool = True

    def voice_for(self, language: str) -> str | None:
        return self.voice_en if language == "en" else self.voice_bn


def plan(request: Request) -> list[tuple[str, str]]:
    """Split a request into ordered synthesis jobs."""
    text = (request.text or "").strip()
    if not text:
        return []

    segments = (
        [(request.language, text)]
        if request.language and request.language != "auto"
        else lang_detect.split_segments(text)
    )

    jobs: list[tuple[str, str]] = []
    for language, segment in segments:
        prepared = (
            normalize_bn.normalize(segment) if language == "bn" else chunker.collapse(segment)
        )
        if not prepared:
            continue
        jobs.extend((language, chunk) for chunk in chunker.split(prepared, language))
    return jobs
