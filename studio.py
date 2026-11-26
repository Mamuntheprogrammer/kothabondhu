"""Kotha Bondhu (কথাবন্ধু) — synthesis service (the non-blocking core).

Everything time-consuming happens here, on background threads:

* a single :class:`Studio` owns a queue and one synthesis worker, so audio is
  produced in order without ever touching a UI thread;
* each :class:`Job` keeps its events and audio in memory, and can be streamed
  to any number of subscribers (the browser connects over SSE);
* model downloads run on their own threads and publish progress events.

The speech engines, DSP helpers and text planner live in the local :mod:`core`
package, so this project runs without any external TTS package.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import threading
import time
import uuid
import wave
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core import __version__ as CORE_VERSION  # noqa: E402
from core.audio import dsp  # noqa: E402
from core.audio import export as audio_export  # noqa: E402
from core.config import OUTPUT_SAMPLE_RATE, cache_dir  # noqa: E402
from core.engines import EmptyText, EngineError, ModelNotAvailable, registry  # noqa: E402
from core.models import manager  # noqa: E402
from core.pipeline import Request, plan  # noqa: E402
import bn_voices  # noqa: E402
from cloning import CloneManager  # noqa: E402

TERMINAL = {"done", "error", "cancelled"}
SAMPLE_RATE = OUTPUT_SAMPLE_RATE
MAX_JOBS = 24
JOB_TTL = 60 * 60 * 2.0  # two hours of replayable audio
PREVIEW_TEXT = "Hello! This is how I sound when I read your words."
PREVIEW_CACHE: dict[str, bytes] = {}
# Two ordinary chunks (~240 chars each) are read in one breath when "steady"
# mode is on. Long enough for natural prosody, short enough to keep streaming.
STEADY_BN_CHARS = 480


# --------------------------------------------------------------------------- wav


def _quick_states(spec) -> dict[str, bool]:
    """Presence and size check, without hashing the model bytes."""
    names = manager.GENERATED_FILES if spec.generated else tuple(f.name for f in spec.files)
    states: dict[str, bool] = {}
    for name in names:
        path = spec.directory / name
        try:
            ok = path.is_file() and path.stat().st_size > 0
            if ok and not spec.generated:
                expected = spec.file(name).bytes
                if expected is not None and path.stat().st_size != expected:
                    ok = False
        except OSError:
            ok = False
        states[name] = ok
    return states


def wav_bytes(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Encode mono float32 audio as a 16-bit PCM WAV, in memory."""
    flat = np.asarray(audio, dtype=np.float32).reshape(-1)
    pcm = (np.clip(flat, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm)
    return buf.getvalue()


def steady_bn_plan(jobs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Coalesce adjacent Bangla fragments into longer synthesis blocks.

    The normal planner splits on every sentence/newline and each fragment is
    synthesized independently, so pitch and rhythm restart each time. Joining
    neighbouring Bangla fragments gives the engine one continuous passage to
    read, which sounds far steadier. Other languages and mixed-language order
    are preserved, and the 480-character ceiling keeps streaming responsive.
    """
    result: list[tuple[str, str]] = []
    pending: list[str] | None = None
    for language, text in jobs:
        if language == "bn":
            if pending is not None and len(pending[1]) + len(text) + 1 <= STEADY_BN_CHARS:
                pending[1] = f"{pending[1]} {text}"
            else:
                if pending is not None:
                    result.append((pending[0], pending[1]))
                pending = ["bn", text]
        else:
            if pending is not None:
                result.append((pending[0], pending[1]))
                pending = None
            result.append((language, text))
    if pending is not None:
        result.append((pending[0], pending[1]))
    return result


# --------------------------------------------------------------------------- jobs


class Job:
    """One synthesis run: request, ordered events, and the produced audio."""

    def __init__(self, request: Request) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.request = request
        self.text = request.text
        self.status = "queued"
        self.created = time.time()
        self.started = 0.0
        self.finished_at = 0.0
        self.total = 0
        self.languages: list[str] = []
        self.chunks: list[np.ndarray] = []
        self.chunk_wavs: list[bytes] = []
        self.chunk_langs: list[str] = []
        self.chunk_texts: list[str] = []
        self.chunk_seconds: list[float] = []
        self.seconds = 0.0
        self.error = ""
        self.cancel_requested = threading.Event()
        self._events: list[dict] = []
        self._cond = threading.Condition()

    # ------------------------------------------------------------------ events

    def emit(self, event: dict) -> None:
        with self._cond:
            self._events.append(event)
            self._cond.notify_all()

    def finish(self, status: str, event: dict | None = None) -> None:
        with self._cond:
            if event is not None:
                self._events.append(event)
            if self.status not in TERMINAL:
                self.status = status
                self.finished_at = time.time()
            self._cond.notify_all()

    def emit_chunk(self, lang: str, text: str, audio: np.ndarray) -> int:
        payload = wav_bytes(audio)
        with self._cond:
            index = len(self.chunks)
            self.chunks.append(np.asarray(audio, dtype=np.float32))
            self.chunk_wavs.append(payload)
            self.chunk_langs.append(lang)
            self.chunk_texts.append(text)
            self.chunk_seconds.append(round(audio.size / SAMPLE_RATE, 3))
            self._events.append(
                {
                    "type": "chunk",
                    "index": index,
                    "language": lang,
                    "text": text,
                    "seconds": self.chunk_seconds[-1],
                    "url": f"/api/jobs/{self.id}/chunks/{index}",
                }
            )
            self._cond.notify_all()
        return index

    def next_events(self, cursor: int, timeout: float = 15.0) -> tuple[list[dict], str, int]:
        """Return ``(events, status, new_cursor)``; waits briefly for new work."""
        with self._cond:
            if cursor >= len(self._events) and self.status not in TERMINAL:
                self._cond.wait(timeout)
            events = self._events[cursor:]
            return events, self.status, cursor + len(events)

    # --------------------------------------------------------------- snapshots

    @property
    def duration(self) -> float:
        return round(sum(self.chunk_seconds), 3)

    def snapshot(self) -> dict:
        with self._cond:
            return {
                "id": self.id,
                "status": self.status,
                "text": self.text,
                "total": self.total,
                "languages": self.languages,
                "duration": self.duration,
                "seconds": self.seconds,
                "error": self.error,
                "clone_voice": getattr(self.request, "clone_voice", None),
                "chunks": [
                    {
                        "index": i,
                        "language": self.chunk_langs[i],
                        "text": self.chunk_texts[i],
                        "seconds": self.chunk_seconds[i],
                        "url": f"/api/jobs/{self.id}/chunks/{i}",
                    }
                    for i in range(len(self.chunks))
                ],
            }

    def summary(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "language": self.request.language,
            "voice": getattr(self.request, "clone_voice", None) or self.request.voice_en,
            "speed": self.request.speed,
            "clone_voice": getattr(self.request, "clone_voice", None),
            "chars": len(self.text),
            "duration": self.duration,
            "created": self.created,
        }


# --------------------------------------------------------------------------- studio


class DownloadState:
    def __init__(self, key: str) -> None:
        self.key = key
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


class Studio:
    """Owns the job queue, the synthesis worker and the model cache view."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._queue: deque[Job] = deque()
        self._active: Job | None = None
        self._jobs: "OrderedDict[str, Job]" = OrderedDict()
        self._engine_lock = threading.RLock()
        self._status_lock = threading.Lock()
        self._voices_lock = threading.Lock()
        self._status_cache: dict | None = None
        self._status_at = 0.0
        self._voices_cache: list[dict] | None = None
        self._downloads: dict[str, DownloadState] = {}
        self._downloads_lock = threading.Lock()
        # Optional feature: constructed eagerly, but it creates no venv, model
        # or subprocess until the user opts in through the Clone panel.
        self.clone = CloneManager()
        self._worker = threading.Thread(target=self._loop, name="kotha-bondhu", daemon=True)
        self._worker.start()

    # ------------------------------------------------------------------ jobs

    def submit(self, request: Request) -> Job:
        job = Job(request)
        with self._cond:
            for queued in list(self._queue):
                queued.cancel_requested.set()
                queued.finish("cancelled", {"type": "cancelled", "reason": "superseded"})
            self._queue.clear()
            if self._active is not None:
                self._active.cancel_requested.set()
                if getattr(self._active.request, "clone_voice", None):
                    # Inference cannot be interrupted inside the worker, so a
                    # superseding Speak terminates that subprocess instead.
                    self.clone.engine.cancel()
            self._queue.append(job)
            self._jobs[job.id] = job
            self._cond.notify_all()
        self._prune()
        return job

    def cancel(self, job_id: str) -> bool:
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            job.cancel_requested.set()
            if job is self._active and getattr(job.request, "clone_voice", None):
                self.clone.engine.cancel()
            if job.status == "queued":
                try:
                    self._queue.remove(job)
                except ValueError:
                    pass
                job.finish("cancelled", {"type": "cancelled", "reason": "user"})
            return True

    def get_job(self, job_id: str) -> Job | None:
        with self._cond:
            return self._jobs.get(job_id)

    def recent_jobs(self) -> list[dict]:
        with self._cond:
            return [job.summary() for job in reversed(self._jobs.values())]

    def _prune(self) -> None:
        now = time.time()
        with self._cond:
            stale = [
                key
                for key, job in self._jobs.items()
                if job.status in TERMINAL and (now - job.finished_at) > JOB_TTL
            ]
            while len(self._jobs) > MAX_JOBS:
                oldest = next(iter(self._jobs))
                if self._jobs[oldest] is self._active:
                    break
                stale.append(oldest)
            for key in stale:
                self._jobs.pop(key, None)

    def _loop(self) -> None:
        while True:
            with self._cond:
                while not self._queue:
                    self._cond.wait()
                job = self._queue.popleft()
                if job.cancel_requested.is_set():
                    job.finish("cancelled", {"type": "cancelled", "reason": "superseded"})
                    continue
                self._active = job
            try:
                self._run(job)
            except Exception as exc:  # noqa: BLE001 — never kill the worker
                job.error = f"{type(exc).__name__}: {exc}"
                job.finish("error", {"type": "error", "message": job.error})
            finally:
                with self._cond:
                    if self._active is job:
                        self._active = None

    def _run(self, job: Job) -> None:
        request = job.request
        started = time.perf_counter()
        job.started = time.time()
        job.status = "running"
        job.emit({"type": "stage", "stage": "prepare", "message": "Splitting text"})

        jobs = plan(request)
        if not jobs:
            raise EmptyText("Nothing to say — the text is empty.")
        if getattr(request, "steady_bn", True):
            jobs = steady_bn_plan(jobs)

        mix = request.loudness_match and len({lang for lang, _ in jobs}) > 1
        job.total = len(jobs)
        job.languages = sorted({lang for lang, _ in jobs})
        job.emit(
            {
                "type": "plan",
                "total": len(jobs),
                "mixed": mix,
                "languages": job.languages,
                "segments": [{"language": lang, "text": text} for lang, text in jobs],
            }
        )

        produced = 0
        for index, (language, text) in enumerate(jobs):
            if job.cancel_requested.is_set():
                job.finish("cancelled", {"type": "cancelled", "reason": "user"})
                return

            clone_voice = getattr(request, "clone_voice", None)
            clone_here = clone_voice and not (
                getattr(request, "clone_bn_only", True) and language != "bn"
            )
            if clone_here:
                # IndicF5 is Bengali-only. Keep the cloned timbre for Bengali
                # chunks; English chunks use the proper English engine so they
                # are pronounced correctly instead of as romanized Bengali.
                engine = self.clone.engine
                selected_voice = clone_voice
            elif language == "bn" and request.voice_bn in bn_voices.VOICES:
                # The two fixed male/female voices live beside the app, not in
                # the core registry. MMS remains the no-selection default.
                engine = bn_voices.ENGINE
                selected_voice = request.voice_bn
            else:
                engine = registry.get_engine(language)
                selected_voice = request.voice_for(language)
            if not engine.is_loaded:
                job.emit(
                    {
                        "type": "stage",
                        "stage": "loading",
                        "engine": engine.name,
                        "language": language,
                        "message": f"Preparing {engine.name}…",
                        "fraction": None,
                    }
                )
                with self._engine_lock:
                    engine.load(progress=self._load_progress(job, engine, language))
                if job.cancel_requested.is_set():
                    job.finish("cancelled", {"type": "cancelled", "reason": "user"})
                    return

            job.emit(
                {
                    "type": "chunk_start",
                    "index": index,
                    "total": len(jobs),
                    "language": language,
                }
            )
            with self._engine_lock:
                try:
                    audio = engine.synthesize(
                        text, voice=selected_voice, speed=request.speed
                    )
                except EngineError:
                    if job.cancel_requested.is_set():
                        job.finish("cancelled", {"type": "cancelled", "reason": "user"})
                        return
                    raise
            if job.cancel_requested.is_set():
                job.finish("cancelled", {"type": "cancelled", "reason": "user"})
                return

            if audio.size == 0:
                job.emit(
                    {
                        "type": "warning",
                        "message": f"Skipped a fragment with no pronounceable text: {text[:48]!r}",
                    }
                )
                continue

            if engine.sample_rate and engine.sample_rate != SAMPLE_RATE:
                audio = dsp.resample(audio, engine.sample_rate, SAMPLE_RATE)
            if mix:
                audio = dsp.match_loudness(audio, -20.0)

            job.emit_chunk(language, text, np.asarray(audio, dtype=np.float32))
            produced += 1

        if produced == 0:
            raise EmptyText(
                "No pronounceable text for this language — check the language "
                "selector or the script of your text."
            )

        job.seconds = round(time.perf_counter() - started, 2)
        job.finish(
            "done",
            {
                "type": "done",
                "chunks": produced,
                "duration": job.duration,
                "seconds": job.seconds,
            },
        )

    def _load_progress(self, job: Job, engine, language: str):
        def cb(stage: str, done: int, total: int) -> None:
            fraction = (done / total) if total else None
            job.emit(
                {
                    "type": "stage",
                    "stage": "loading",
                    "engine": engine.name,
                    "language": language,
                    "message": f"{engine.name}: {stage}",
                    "fraction": fraction,
                }
            )

        return cb

    # ---------------------------------------------------------------- preview

    def preview(self, voice: str) -> bytes:
        if voice in PREVIEW_CACHE:
            return PREVIEW_CACHE[voice]
        if voice in bn_voices.VOICES:
            # The packaged sample clips are faster than loading the model and
            # are representative of each fixed speaker.
            payload = bn_voices.sample(voice)
            PREVIEW_CACHE[voice] = payload
            return payload
        engine = registry.get_engine("en")
        if not manager.is_ready("kokoro"):
            raise ModelNotAvailable("The English model is not downloaded yet.")
        with self._engine_lock:
            if not engine.is_loaded:
                engine.load()
            audio = engine.synthesize(PREVIEW_TEXT, voice=voice, speed=1.0)
        payload = wav_bytes(audio)
        PREVIEW_CACHE[voice] = payload
        return payload

    # ----------------------------------------------------------------- models

    def models(self, force: bool = False) -> dict:
        now = time.monotonic()
        with self._status_lock:
            # Model files change only on download, which calls invalidate().
            # Hashing hundreds of megabytes must not happen on every page load.
            if not force and self._status_cache and now - self._status_at < 300.0:
                return self._status_cache
            result = self._build_models(verify=force)
            self._status_cache = result
            self._status_at = now
            return result

    def _build_models(self, verify: bool = False) -> dict:
        models = []
        for key in manager.SPECS:
            spec = manager.get_spec(key)
            try:
                # Normal page loads only need presence + size, which is instant.
                # "Re-check" opts into full SHA-256 verification.
                states = manager.status(key) if verify else _quick_states(spec)
                # Derive readiness from the same status pass instead of hashing
                # the files a second time through manager.is_ready().
                required = tuple(states) if spec.generated else spec.wanted
                ready = all(states.get(name, False) for name in required)
            except Exception as exc:  # noqa: BLE001
                states, ready = {}, False
                error = str(exc)
            else:
                error = ""
            wanted = set(spec.wanted) | (set(states) if spec.generated else set())
            expected = 0
            for remote in spec.files:
                if remote.name in wanted:
                    expected += remote.bytes or 0
            cached = 0
            for name in wanted:
                path = spec.directory / name
                if path.exists():
                    cached += path.stat().st_size
            models.append(
                {
                    "key": key,
                    "title": spec.title,
                    "languages": list(spec.languages),
                    "ready": ready,
                    "generated": spec.generated,
                    "bytes": expected or cached,
                    "cached_bytes": cached,
                    "missing": [name for name, ok in states.items() if not ok],
                    "error": error,
                }
            )
        models.append(bn_voices.status())
        return {"models": models, "cache_dir": str(cache_dir())}

    def invalidate(self) -> None:
        with self._status_lock:
            self._status_at = 0.0
        with self._voices_lock:
            self._voices_cache = None
        PREVIEW_CACHE.clear()

    def voices(self) -> list[dict]:
        with self._voices_lock:
            if self._voices_cache is not None:
                return self._voices_cache
            try:
                engine = registry.get_engine("en")
                raw = engine.voices()
            except Exception:  # noqa: BLE001
                raw = []
            voices = []
            for voice in raw:
                label = engine.voice_label(voice)
                name, _, style = label.partition(" · ")
                voices.append(
                    {
                        "id": voice,
                        "label": label,
                        "name": name or voice,
                        "style": style or "English",
                        "group": style or "English",
                        "language": "en",
                    }
                )
            # Fixed Bangla choices: the original MMS speaker plus the bundled
            # male/female model. They are advertised even before the extra
            # model is downloaded; the UI checks readiness before synthesis.
            voices.append(
                {
                    "id": bn_voices.MMS_VOICE_ID,
                    "label": "MMS · single speaker",
                    "name": "MMS · single speaker",
                    "style": "Bangla · default",
                    "group": "Bangla voices",
                    "language": "bn",
                }
            )
            for voice_id, (_, name, style) in bn_voices.VOICES.items():
                voices.append(
                    {
                        "id": voice_id,
                        "label": bn_voices.voice_label(voice_id),
                        "name": name,
                        "style": style,
                        "group": "Bangla voices",
                        "language": "bn",
                    }
                )
            order = {
                "Bangla voices": 0,
                "US female": 1,
                "US male": 2,
                "UK female": 3,
                "UK male": 4,
            }
            voices.sort(key=lambda v: (order.get(v["group"], 9), v["name"].lower()))
            self._voices_cache = voices
            return voices

    def status_payload(self, force: bool = False) -> dict:
        models = self.models(force=force)
        return {
            "ok": True,
            "core_version": CORE_VERSION,
            "app": "Kotha Bondhu (কথাবন্ধু)",
            "sample_rate": SAMPLE_RATE,
            "cache_dir": models["cache_dir"],
            "models": models["models"],
            "voices": self.voices(),
            "default_voice": "af_heart",
            "clone": self.clone.status(),
            "recent": self.recent_jobs()[:8],
        }

    # --------------------------------------------------------------- download

    def download_stream(self, key: str) -> Iterator[dict]:
        if key == bn_voices.MODEL_KEY:
            yield from bn_voices.MANAGER.download_stream()
            return
        if key not in manager.SPECS:
            yield {"type": "error", "message": f"Unknown model {key!r}"}
            return
        spec = manager.get_spec(key)
        if manager.is_ready(key):
            yield {"type": "done", "ready": True, "message": f"{spec.title} is ready."}
            return

        with self._downloads_lock:
            state = self._downloads.get(key)
            fresh = state is None or state.status != "running"
            if fresh:
                state = DownloadState(key)
                self._downloads[key] = state

        if fresh:
            threading.Thread(
                target=self._download_worker,
                args=(key, state),
                name=f"download-{key}",
                daemon=True,
            ).start()

        cursor = 0
        idle = 0.0
        while True:
            events, status, cursor = state.next_events(cursor)
            for event in events:
                yield event
            if status != "running":
                break
            if not events:
                idle += 1.0
                if idle >= 1.0:
                    yield {"type": "ping"}
                    idle = 0.0

    def _download_worker(self, key: str, state: DownloadState) -> None:
        spec = manager.get_spec(key)
        state.emit(
            {
                "type": "start",
                "key": key,
                "title": spec.title,
                "bytes": sum(f.bytes or 0 for f in spec.files if f.name in spec.wanted),
            }
        )
        def progress(done: int, total: int, label: str) -> None:
            state.emit(
                {
                    "type": "progress",
                    "done": int(done),
                    "total": int(total),
                    "label": label,
                    "percent": round(100.0 * done / total, 1) if total else None,
                }
            )

        try:
            manager.ensure_threaded(key, progress=progress)
        except Exception as exc:  # noqa: BLE001
            state.finish("error", {"type": "error", "message": str(exc)})
            self.invalidate()
            return
        self.invalidate()
        state.finish(
            "done",
            {
                "type": "done",
                "ready": manager.is_ready(key),
                "message": f"{spec.title} downloaded.",
            },
        )

    # ------------------------------------------------------------------ export

    def export(self, job: Job, fmt: str) -> Path:
        fmt = (fmt or "wav").lower().lstrip(".")
        if fmt not in {"wav", "flac", "mp3", "ogg"}:
            raise ValueError(f"Unsupported format: {fmt}")
        if not job.chunks:
            raise ValueError("This job has no audio yet.")
        audio = np.concatenate(job.chunks) if len(job.chunks) > 1 else job.chunks[0]
        out_dir = Path(cache_dir()) / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = out_dir / f"kotha-bondhu-{stamp}-{job.id}.{fmt}"
        audio_export.export(path, [audio], SAMPLE_RATE)
        return path


# --------------------------------------------------------------------------- repair


def repair_bangla_manifest() -> bool:
    """Rebuild a missing ``manifest.json`` for an already-exported MMS model.

    Older caches shipped ``model.onnx`` + ``vocab.json`` without the manifest,
    which makes the Bangla engine look unavailable. Everything needed to
    describe the model is derivable from the exported files, so the studio can
    heal that cache on startup instead of asking for a 500 MB re-download.
    """
    try:
        spec = manager.get_spec("mms-ben-onnx")
    except KeyError:
        return False
    directory = spec.directory
    manifest = directory / "manifest.json"
    onnx = directory / "model.onnx"
    vocab = directory / "vocab.json"
    if manifest.exists() or not onnx.exists() or not vocab.exists():
        return False

    sample_rate = 16_000
    config_paths = (directory / ".hf" / "config.json", directory / "config.json")
    for config_path in config_paths:
        if not config_path.exists():
            continue
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        try:
            sample_rate = int(config.get("sampling_rate") or sample_rate)
        except (TypeError, ValueError):
            pass
        break

    files = {}
    for name in ("model.onnx", "model.int8.onnx"):
        path = directory / name
        if path.exists() and path.stat().st_size > 0:
            files[name] = {"bytes": path.stat().st_size}
    if "model.onnx" not in files:
        return False

    data = {
        "model_id": "facebook/mms-tts-ben",
        "sample_rate": sample_rate,
        "files": files,
        "repaired_from_cache": True,
        "created_unix": int(time.time()),
    }
    try:
        manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        return False
    return True


def open_in_file_manager(path: Path) -> None:
    import os
    import subprocess

    target = str(path)
    if sys.platform.startswith("win"):
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


def clear_exports() -> None:
    shutil.rmtree(Path(cache_dir()) / "exports", ignore_errors=True)
