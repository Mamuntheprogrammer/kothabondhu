"""Bangla engine: Meta MMS-TTS Bengali exported to ONNX.

Only ONNX Runtime and a tiny character tokenizer are needed at runtime.
"""

from __future__ import annotations

import json
import os
import threading

import numpy as np

from ..audio import dsp
from ..models import manager
from . import registry
from .base import ModelNotAvailable, Progress, TTSEngine

_SPEC = "mms-ben-onnx"
_MODEL_FP32 = "model.onnx"
_MODEL_INT8 = "model.int8.onnx"
_VARIANTS = ("auto", "int8", "fp32")


def encode_text(text: str, vocab: dict[str, int]) -> list[int]:
    """Tokenize like the MMS VITS tokenizer: longest match, then blank-wrap."""
    if not text:
        return []
    keys = sorted(vocab, key=len, reverse=True)

    pieces: list[str] = []
    index = 0
    while index < len(text):
        for key in keys:
            if text.startswith(key, index):
                pieces.append(key)
                index += len(key)
                break
        else:
            pieces.append(text[index].lower())
            index += 1

    usable = "".join(piece for piece in pieces if piece in vocab).strip()
    if not usable:
        return []

    ids = [vocab[char] for char in usable]
    blank = 0 if 0 in vocab.values() else None
    if blank is None:
        return ids
    result: list[int] = []
    for token in ids:
        result.extend((blank, token))
    result.append(blank)
    return result


def _pick_model(manifest: dict, variant: str) -> str:
    files = manifest.get("files", {})
    if variant == "fp32":
        return _MODEL_FP32
    if variant == "int8":
        if _MODEL_INT8 not in files:
            raise ModelNotAvailable("the int8 Bangla model was not exported")
        return _MODEL_INT8
    return _MODEL_FP32 if files.get(_MODEL_FP32) or _MODEL_FP32 not in files else _MODEL_INT8


def _forward(progress: Progress | None, stage: str):
    if progress is None:
        return lambda done, total, label: None

    def callback(done: int, total: int, label: str) -> None:
        progress(stage, done, total)

    return callback


class MMSEngine(TTSEngine):
    name = "mms"
    language = "bn"
    default_voice = "default"

    def __init__(self, variant: str | None = None) -> None:
        super().__init__()
        chosen = (variant or os.environ.get("AWAZ_MMS_VARIANT", "auto")).lower()
        self.variant = chosen if chosen in _VARIANTS else "auto"
        self._session = None
        self._input_name = ""
        self._vocab: dict[str, int] = {}
        self._model_file = ""
        self.sample_rate = 16_000
        self._lock = threading.RLock()

    def load(self, progress: Progress | None = None) -> None:
        with self._lock:
            if self._session is not None:
                return
            try:
                directory = manager.ensure_threaded(
                    _SPEC, progress=_forward(progress, "preparing Bangla model")
                )
            except manager.ModelError as exc:
                raise ModelNotAvailable(
                    "The Bangla model is not available in this cache "
                    f"({exc}). Copy models/mms-ben-onnx beside the app."
                ) from exc

            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            vocab = json.loads((directory / "vocab.json").read_text(encoding="utf-8"))
            self.sample_rate = int(manifest.get("sample_rate", 16_000))
            self._vocab = {str(key): int(value) for key, value in vocab.items()}
            self._model_file = _pick_model(manifest, self.variant)

            import onnxruntime as ort

            session = ort.InferenceSession(
                str(directory / self._model_file), providers=["CPUExecutionProvider"]
            )
            self._input_name = session.get_inputs()[0].name
            self._session = session
            self._loaded = True

    def unload(self) -> None:
        with self._lock:
            self._session = None
            self._vocab = {}
        super().unload()

    @property
    def model_file(self) -> str:
        return self._model_file

    def voices(self) -> list[str]:
        return [self.default_voice]

    def voice_label(self, voice: str) -> str:
        return "Single speaker (MMS-TTS)"

    def synthesize(
        self, text: str, voice: str | None = None, speed: float = 1.0
    ) -> np.ndarray:
        if not text or not text.strip():
            return np.zeros(0, dtype=np.float32)
        if self._session is None:
            self.load()

        ids = encode_text(text, self._vocab)
        if not ids:
            return np.zeros(0, dtype=np.float32)

        batch = np.asarray([ids], dtype=np.int64)
        waveform = self._session.run(None, {self._input_name: batch})[0]
        audio = np.asarray(waveform, dtype=np.float32).reshape(-1)
        if abs(speed - 1.0) > 1e-3 and audio.size:
            audio = dsp.change_speed(audio, speed)
        return audio


registry.register("bn", MMSEngine)
