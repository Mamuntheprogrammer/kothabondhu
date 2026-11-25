"""English engine: Kokoro-82M through ``kokoro-onnx`` (ONNX Runtime, CPU)."""

from __future__ import annotations

import os
import threading

import numpy as np

from ..audio import dsp
from ..models import manager
from . import registry
from .base import ModelNotAvailable, Progress, TTSEngine

# Kokoro ships many voices; these prefixes read English well.
_ENGLISH_PREFIXES = ("af", "am", "bf", "bm", "em")
_PREFIX_STYLE = {
    "af": "US female",
    "am": "US male",
    "bf": "UK female",
    "bm": "UK male",
    "em": "US",
}
_MODEL_FILES = {"full": "kokoro-v1.0.onnx", "int8": "kokoro-v1.0.int8.onnx"}
_VOICES_FILE = "voices-v1.0.bin"
_SPEC = "kokoro"
_DEFAULT_VARIANT = os.environ.get("AWAZ_KOKORO_VARIANT", "full")


def _english(voice: str) -> bool:
    return voice.partition("_")[0] in _ENGLISH_PREFIXES


def _forward(progress: Progress | None, stage: str):
    if progress is None:
        return lambda done, total, label: None

    def callback(done: int, total: int, label: str) -> None:
        progress(stage, done, total)

    return callback


class KokoroEngine(TTSEngine):
    name = "kokoro"
    language = "en"
    sample_rate = 24_000

    def __init__(self, variant: str | None = None) -> None:
        super().__init__()
        chosen = (variant or _DEFAULT_VARIANT).lower()
        self.variant = chosen if chosen in _MODEL_FILES else "int8"
        self._model = None
        self._voices: list[str] = []
        self._lock = threading.RLock()

    @property
    def model_file(self) -> str:
        return _MODEL_FILES[self.variant]

    def load(self, progress: Progress | None = None) -> None:
        with self._lock:
            if self._model is not None:
                return
            try:
                directory = manager.ensure_threaded(
                    _SPEC,
                    files=(_VOICES_FILE, self.model_file),
                    progress=_forward(progress, "loading model"),
                )
            except manager.ModelError as exc:
                raise ModelNotAvailable(f"Kokoro is not available: {exc}") from exc

            from kokoro_onnx import Kokoro

            self._model = Kokoro(
                str(directory / self.model_file), str(directory / _VOICES_FILE)
            )
            self._voices = [voice for voice in self._model.get_voices() if _english(voice)]
            self._loaded = True

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._voices = []
        super().unload()

    def voices(self) -> list[str]:
        if self._voices:
            return list(self._voices)
        if not manager.is_ready(_SPEC):
            return []
        try:
            data = np.load(manager.get_spec(_SPEC).directory / _VOICES_FILE)
            self._voices = [voice for voice in data.keys() if _english(voice)]
        except (OSError, ValueError):
            return []
        return list(self._voices)

    @staticmethod
    def voice_label(voice: str) -> str:
        prefix, _, name = voice.partition("_")
        style = _PREFIX_STYLE.get(prefix, prefix)
        return f"{name.title()} · {style}" if name else voice

    def synthesize(
        self, text: str, voice: str | None = None, speed: float = 1.0
    ) -> np.ndarray:
        if not text or not text.strip():
            return np.zeros(0, dtype=np.float32)
        if self._model is None:
            self.load()

        available = self._voices or self.voices()
        chosen = voice if voice in available else None
        if chosen is None:
            chosen = (
                "af_heart"
                if "af_heart" in available
                else (available[0] if available else voice or "af_heart")
            )

        rate = min(max(float(speed), 0.5), 2.0)
        audio, sample_rate = self._model.create(text, chosen, speed=rate, lang="en-us")
        if int(sample_rate) != self.sample_rate:
            audio = dsp.resample(audio, int(sample_rate), self.sample_rate)
        return np.asarray(audio, dtype=np.float32).reshape(-1)


registry.register("en", KokoroEngine)
