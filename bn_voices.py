"""Bundled Bengali voices: a small two-speaker ONNX engine.

The default Bangla path stays MMS (one speaker). This module adds an optional
male/female pair from ``sulabhkatiyar/indic-tiny-tts-bengali`` as fixed voices.
It is deliberately separate from :mod:`awaaz.engines`: the core package is
left untouched, and the model is only imported when one of these voices is
actually selected.

Runtime: ONNX Runtime + NumPy (already used by the studio). Output is 24 kHz
mono float32, so no resampling is needed. ``seed=0`` is passed to the model so
repeated runs are deterministic instead of drifting with random flow noise.

Licence: acoustic weights CC-BY-4.0 (credit Sulabh Katiyar / AI4Bharat Rasa),
vocoder weights MIT (Vocos), runtime code MIT. See LICENSE.md in the model dir.
"""

from __future__ import annotations

import importlib
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator

from awaaz.config import cache_dir
from awaaz.engines import EngineError, ModelNotAvailable

MODEL_KEY = "indic-tiny-bn"
MODEL_TITLE = "Bangla voices"
MODEL_LABEL = "Indic Tiny TTS · female & male"
REPO = "sulabhkatiyar/indic-tiny-tts-bengali"
BASE_URL = f"https://huggingface.co/{REPO}/resolve/main/"

# (relative path, expected bytes) for every file the ONNX runtime needs, plus
# two sample clips used by the voice picker and the licence/attribution file.
# Sizes come from the repository manifest, so a partial download is detectable.
FILES: tuple[tuple[str, int], ...] = (
    ("config.json", 3210),
    ("indic_tiny_tts/__init__.py", 644),
    ("indic_tiny_tts/__main__.py", 52),
    ("indic_tiny_tts/constants.py", 721),
    ("indic_tiny_tts/export/__init__.py", 321),
    ("indic_tiny_tts/export/onnx_infer.py", 12835),
    ("indic_tiny_tts/frontend/__init__.py", 0),
    ("indic_tiny_tts/frontend/en_infer.py", 990),
    ("indic_tiny_tts/frontend/textproc.py", 9179),
    ("indic_tiny_tts/frontend/tokenizer.py", 6186),
    ("indic_tiny_tts/infer.py", 7775),
    ("indic_tiny_tts/latency.py", 22574),
    ("indic_tiny_tts/model/__init__.py", 0),
    ("indic_tiny_tts/model/acoustic.py", 11131),
    ("indic_tiny_tts/model/mas.py", 9585),
    ("indic_tiny_tts/model/vocoder.py", 2117),
    ("indic_tiny_tts/textnorm/__init__.py", 19342),
    ("indic_tiny_tts/textnorm/_inference_tables.py", 45768),
    ("indic_tiny_tts/textnorm/_lexsrc.py", 4601),
    ("indic_tiny_tts/textnorm/_number_tables.py", 17932),
    ("indic_tiny_tts/textnorm/_translit/__init__.py", 266),
    ("indic_tiny_tts/textnorm/_translit/core.py", 7418),
    ("indic_tiny_tts/textnorm/_translit/letters.py", 11035),
    ("indic_tiny_tts/textnorm/_translit/lexicon/as.tsv.gz", 65949),
    ("indic_tiny_tts/textnorm/_translit/lexicon/bn.tsv.gz", 137172),
    ("indic_tiny_tts/textnorm/_translit/lexicon/gu.tsv.gz", 98687),
    ("indic_tiny_tts/textnorm/_translit/lexicon/hi.tsv.gz", 81014),
    ("indic_tiny_tts/textnorm/_translit/lexicon/kn.tsv.gz", 60831),
    ("indic_tiny_tts/textnorm/_translit/lexicon/MANIFEST.json", 3513),
    ("indic_tiny_tts/textnorm/_translit/lexicon/ml.tsv.gz", 103176),
    ("indic_tiny_tts/textnorm/_translit/lexicon/mr.tsv.gz", 99538),
    ("indic_tiny_tts/textnorm/_translit/lexicon/or.tsv.gz", 63831),
    ("indic_tiny_tts/textnorm/_translit/lexicon/pa.tsv.gz", 76859),
    ("indic_tiny_tts/textnorm/_translit/lexicon/ta.tsv.gz", 104091),
    ("indic_tiny_tts/textnorm/_translit/lexicon/te.tsv.gz", 44176),
    ("indic_tiny_tts/textnorm/_translit/NOTICE", 4800),
    ("indic_tiny_tts/textnorm/_translit/rules.py", 10201),
    ("indic_tiny_tts/textnorm/core.py", 26439),
    ("indic_tiny_tts/textnorm/en.py", 27597),
    ("indic_tiny_tts/textnorm/inference.py", 46315),
    ("indic_tiny_tts/textnorm/latin_v1.py", 26106),
    ("indic_tiny_tts/textnorm/latin.py", 44254),
    ("indic_tiny_tts/textnorm/LICENSE-indic_numtowords", 1066),
    ("indic_tiny_tts/textnorm/numbers.py", 37463),
    ("indic_tiny_tts/vendor/__init__.py", 67),
    ("indic_tiny_tts/vendor/matcha/__init__.py", 75),
    ("indic_tiny_tts/vendor/matcha/decoder.py", 13301),
    ("indic_tiny_tts/vendor/matcha/flow_matching.py", 5473),
    ("indic_tiny_tts/vendor/matcha/LICENSE", 1069),
    ("indic_tiny_tts/vendor/matcha/model_utils.py", 1581),
    ("indic_tiny_tts/vendor/matcha/text_encoder.py", 16036),
    ("indic_tiny_tts/vendor/matcha/transformer.py", 17091),
    ("indic_tiny_tts/vendor/matcha/UPSTREAM.txt", 3658),
    ("indic_tiny_tts/vendor/vocos/__init__.py", 66),
    ("indic_tiny_tts/vendor/vocos/heads.py", 2733),
    ("indic_tiny_tts/vendor/vocos/LICENSE", 1069),
    ("indic_tiny_tts/vendor/vocos/models.py", 4673),
    ("indic_tiny_tts/vendor/vocos/modules.py", 7919),
    ("indic_tiny_tts/vendor/vocos/spectral_ops.py", 3223),
    ("indic_tiny_tts/vendor/vocos/UPSTREAM.txt", 1254),
    ("LICENSE.md", 3335),
    ("onnx/flow_decoder.onnx", 19169253),
    ("onnx/onnx_manifest.json", 3119),
    ("onnx/text_encoder.onnx", 17692254),
    ("onnx/vocoder.onnx", 54150036),
    ("samples/female_1.wav", 346668),
    ("samples/male_1.wav", 225836),
)

# Studio voice id -> (upstream voice name, display name, style label).
VOICES: dict[str, tuple[str, str, str]] = {
    "bn_female": ("female", "Bangla Female", "Bangla · female"),
    "bn_male": ("male", "Bangla Male", "Bangla · male"),
}
MMS_VOICE_ID = "mms_bn"
SAMPLES = {"bn_female": "samples/female_1.wav", "bn_male": "samples/male_1.wav"}
EXPECTED_BYTES = sum(size for _, size in FILES)


def model_dir() -> Path:
    return cache_dir() / MODEL_KEY


def _state(name: str, expected: int) -> bool:
    path = model_dir() / name
    try:
        return path.is_file() and path.stat().st_size == expected
    except OSError:
        return False


def file_states() -> dict[str, bool]:
    return {name: _state(name, size) for name, size in FILES}


def is_ready() -> bool:
    states = file_states()
    return all(states.values()) and (model_dir() / "config.json").is_file()


def status() -> dict:
    states = file_states()
    cached = 0
    for name in states:
        try:
            cached += (model_dir() / name).stat().st_size
        except OSError:
            pass
    return {
        "key": MODEL_KEY,
        "title": MODEL_TITLE,
        "languages": ["bn"],
        "ready": all(states.values()),
        "generated": False,
        "bytes": EXPECTED_BYTES,
        "cached_bytes": cached,
        "missing": [name for name, ok in states.items() if not ok],
        "error": "",
    }


def voice_label(voice_id: str) -> str:
    _, name, style = VOICES.get(voice_id, ("", voice_id, "Bangla"))
    return f"{name} · {style}"


def sample(voice_id: str) -> bytes:
    name = SAMPLES.get(voice_id)
    if not name:
        raise KeyError(f"No sample for voice {voice_id!r}")
    path = model_dir() / name
    if not path.is_file():
        raise ModelNotAvailable("The Bangla voice model is not downloaded yet.")
    return path.read_bytes()


class DownloadTask:
    """Small SSE-compatible event queue shared by the downloader and server."""

    def __init__(self) -> None:
        self.status = "running"
        self.error = ""
        self.events: list[dict] = []
        self.cond = threading.Condition()

    def emit(self, event: dict) -> None:
        with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    def finish(self, status: str, event: dict | None = None) -> None:
        with self.cond:
            if event is not None:
                self.events.append(event)
            self.status = status
            self.cond.notify_all()

    def next_events(self, cursor: int, timeout: float = 15.0) -> tuple[list[dict], str, int]:
        with self.cond:
            if cursor >= len(self.events) and self.status == "running":
                self.cond.wait(timeout)
            events = self.events[cursor:]
            return events, self.status, cursor + len(events)


class Manager:
    """Downloads and caches the two-voice model without blocking the UI."""

    def __init__(self) -> None:
        self._task: DownloadTask | None = None
        self._lock = threading.Lock()

    def download_stream(self) -> Iterator[dict]:
        with self._lock:
            fresh = self._task is None or self._task.status != "running"
            if fresh:
                self._task = DownloadTask()
            task = self._task
        if fresh:
            threading.Thread(
                target=self._download_worker,
                args=(task,),
                name="download-indic-tiny-bn",
                daemon=True,
            ).start()

        cursor = 0
        idle = 0.0
        while True:
            events, status, cursor = task.next_events(cursor)
            for event in events:
                yield event
            if status != "running":
                break
            if not events:
                idle += 1.0
                if idle >= 1.0:
                    yield {"type": "ping"}
                    idle = 0.0

    def _download_worker(self, task: DownloadTask) -> None:
        directory = model_dir()
        directory.mkdir(parents=True, exist_ok=True)
        task.emit({"type": "start", "key": MODEL_KEY, "title": MODEL_TITLE, "bytes": EXPECTED_BYTES})
        progress = [0]
        try:
            for name, size in FILES:
                target = directory / name
                if target.is_file() and target.stat().st_size == size:
                    progress[0] += size
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                self._download_one(name, size, target, EXPECTED_BYTES, progress, task)
        except Exception as exc:  # noqa: BLE001 — reported over SSE
            ENGINE.unload()
            task.finish("error", {"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            return
        ENGINE.unload()
        task.finish(
            "done",
            {"type": "done", "ready": is_ready(), "message": "Bangla male and female voices are ready."},
        )

    @staticmethod
    def _download_one(
        name: str,
        size: int,
        target: Path,
        total: int,
        progress: list[int],
        task: DownloadTask,
    ) -> None:
        part = target.with_name(target.name + ".part")
        existing = part.stat().st_size if part.is_file() else 0
        if existing > size:
            part.unlink(missing_ok=True)
            existing = 0
        headers = {"User-Agent": "KothaBondhu/2.2", "Accept": "application/octet-stream"}
        if existing:
            headers["Range"] = f"bytes={existing}-"
        request = urllib.request.Request(BASE_URL + name, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=60)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and part.is_file():
                part.unlink(missing_ok=True)
                return Manager._download_one(name, size, target, total, progress, task)
            raise RuntimeError(f"Could not download {name}: HTTP {exc.code}") from exc
        mode = "ab"
        with response:
            if existing and response.status != 206:
                part.unlink(missing_ok=True)
                existing = 0
                mode = "wb"
            with open(part, mode) as handle:
                last = 0.0
                while True:
                    block = response.read(262_144)
                    if not block:
                        break
                    handle.write(block)
                    progress[0] += len(block)
                    now = time.monotonic()
                    if now - last >= 0.15:
                        task.emit(
                            {
                                "type": "progress",
                                "done": progress[0],
                                "total": total,
                                "percent": round(100.0 * progress[0] / total, 1) if total else None,
                                "label": name,
                            }
                        )
                        last = now
        actual = part.stat().st_size
        if actual != size:
            raise RuntimeError(f"Incomplete download: {name} ({actual} of {size} bytes). Retry to resume.")
        os.replace(part, target)
        task.emit({"type": "log", "line": f"Saved {name} ({actual / 1e6:.1f} MB)"})


class Engine:
    """Lazy ONNX wrapper implementing the studio's engine contract."""

    name = MODEL_KEY
    language = "bn"
    sample_rate = 24_000
    default_voice = "bn_female"

    def __init__(self) -> None:
        self._tts = None
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def unload(self) -> None:
        self._tts = None
        self._loaded = False

    def load(self, progress=None) -> None:
        if self._loaded:
            return
        if not is_ready():
            raise ModelNotAvailable(
                "The Bangla male/female model is not downloaded yet. "
                "Open Models → Bangla voices to fetch it."
            )
        folder = str(model_dir())
        if folder not in sys.path:
            sys.path.insert(0, folder)
        importlib.invalidate_caches()
        try:
            module = importlib.import_module("indic_tiny_tts.export.onnx_infer")
        except Exception as exc:  # noqa: BLE001
            raise EngineError(
                f"Could not load the Bangla voice runtime ({type(exc).__name__}: {exc}). "
                "Install onnxruntime, then restart the studio."
            ) from exc
        try:
            self._tts = module.TinyTTSOnnx.from_pretrained(folder, threads=2)
        except Exception as exc:  # noqa: BLE001
            raise EngineError(f"Could not load the Bangla voices: {exc}") from exc
        self._loaded = True

    def voices(self) -> list[str]:
        return list(VOICES) if is_ready() else []

    def voice_label(self, voice: str) -> str:
        return voice_label(voice)

    def synthesize(self, text: str, voice: str | None = None, speed: float = 1.0):
        self.load()
        upstream = VOICES.get(voice or self.default_voice)
        if upstream is None:
            raise EngineError(f"Unknown Bangla voice {voice!r}.")
        try:
            # A fixed seed keeps the flow-decoder noise stable between lines.
            return self._tts.synthesize(text, voice=upstream[0], speed=speed, seed=0)
        except Exception as exc:  # noqa: BLE001
            raise EngineError(f"Bangla synthesis failed: {exc}") from exc


MANAGER = Manager()
ENGINE = Engine()
