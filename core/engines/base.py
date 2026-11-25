"""The common interface every speech backend implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

import numpy as np

# (stage name, work done, work total) — used for download/load progress events.
Progress = Callable[[str, int, int], None]


class EngineError(RuntimeError):
    """Base class for failures inside an engine."""


class ModelNotAvailable(EngineError):
    """The model files are missing or unusable."""


class UnsupportedLanguage(EngineError):
    """No engine is registered for the requested language."""


class EmptyText(EngineError):
    """Nothing pronounceable survived normalization."""


class TTSEngine(ABC):
    """A lazily loaded, single-language speech backend."""

    name: str = ""
    language: str = ""
    sample_rate: int = 0
    default_voice: str = ""

    def __init__(self) -> None:
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @abstractmethod
    def load(self, progress: Progress | None = None) -> None:
        """Make the engine usable. Safe to call more than once."""

    @abstractmethod
    def voices(self) -> list[str]:
        """Voice ids available on this machine (may be empty before download)."""

    @abstractmethod
    def synthesize(
        self, text: str, voice: str | None = None, speed: float = 1.0
    ) -> np.ndarray:
        """Return mono float32 audio at ``sample_rate``."""

    def unload(self) -> None:
        self._loaded = False

    def voice_label(self, voice: str) -> str:
        return voice

    def describe(self) -> dict:
        return {
            "name": self.name,
            "language": self.language,
            "sample_rate": self.sample_rate,
            "loaded": self._loaded,
        }
