"""Optional, on-demand voice cloning for Kotha Bondhu (কথাবন্ধু).

This module is deliberately separate from the lightweight default path. The
bundled Kokoro/MMS engines need nothing from here: cloning code is imported, but
no clone dependency, venv, model or worker is created until the user opens the
Clone panel and asks for it.

The implementation has three pieces:

* :class:`CloneManager` — creates an isolated virtual environment, downloads the
  IndicF5 weights and vocoder with plain stdlib HTTP, and stores reference
  voices under ``<cache>/cloning/``;
* :class:`CloneEngine` — a :class:`~core.engines.base.TTSEngine` that talks to a
  long-lived worker subprocess over JSON lines;
* ``clone_worker.py`` — the subprocess, which is the only place the heavy
  PyTorch stack is imported. A crash or OOM there cannot take down the studio.

The Hugging Face token is kept in server memory for the lifetime of the process
only. It is never written to disk, logged, or returned to the browser.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import wave
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np
import urllib.error
import urllib.request

from core.audio import dsp
from core.config import cache_dir
from core.engines.base import EngineError, ModelNotAvailable, TTSEngine
from core.pipeline import Request

BASE_DIR = Path(__file__).resolve().parent
WORKER = BASE_DIR / "clone_worker.py"

REPO_ID = os.environ.get("AWAZ_CLONE_REPO") or "ai4bharat/IndicF5"
VOCODER_REPO = "charactr/vocos-mel-24khz"
HF_API = "https://huggingface.co/api/models/{repo}?blobs=true"
HF_RESOLVE = "https://huggingface.co/{repo}/resolve/main/{filename}"
MOCK = os.environ.get("AWAZ_CLONE_MOCK") == "1"

# Only the files actually needed for inference are fetched. The upstream repo
# also contains training/evaluation scripts and example audio; those are not
# required at runtime and would only make the optional setup slower.
MODEL_FILES = (
    "config.json",
    "model.py",
    "model.safetensors",
    "checkpoints/vocab.txt",
    "f5_tts/infer/utils_infer.py",
    "f5_tts/model/cfm.py",
    "f5_tts/model/modules.py",
    "f5_tts/model/utils.py",
    "f5_tts/model/backbones/dit.py",
)
VOCODER_FILES = ("config.yaml", "pytorch_model.bin")

# Used only until sizes.json is written by the downloader (or when the user
# copied a model folder across without that small manifest).
KNOWN_SIZES = {
    "model.safetensors": 1_402_789_408,
    "pytorch_model.bin": 54_365_991,
    "config.json": 350,
    "model.py": 5_774,
    "checkpoints/vocab.txt": 11_283,
    "f5_tts/infer/utils_infer.py": 18_459,
    "f5_tts/model/cfm.py": 9_415,
    "f5_tts/model/modules.py": 21_737,
    "f5_tts/model/utils.py": 6_355,
    "f5_tts/model/backbones/dit.py": 5_240,
    "config.yaml": 461,
}

PIP_EXTRA = (
    "accelerate>=0.33.0",
    "transformers<4.50",
    "numpy<=1.26.4",
    "librosa",
    "soundfile",
    "pydub",
    "safetensors",
    "huggingface_hub>=0.24",
    "vocos",
    "x_transformers>=1.31.14",
    "torchdiffeq",
    "jieba",
    "pypinyin",
    "matplotlib",
    "tqdm>=4.65.0",
)

CPU_WARNING = (
    "IndicF5 is a 350M-parameter diffusion model. On a CPU it can take several "
    "minutes per sentence; a CUDA GPU is strongly recommended."
)


@dataclass
class CloneRequest(Request):
    """A normal studio request that routes through the optional clone engine."""

    clone_voice: str | None = None
    #: Merge adjacent Bangla fragments so pitch stays steadier across lines.
    steady_bn: bool = True


# --------------------------------------------------------------------------- helpers


def _clean_id(value: str) -> str:
    value = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("Invalid voice id.")
    return value


def _venv_python(venv: Path) -> Path:
    if sys.platform.startswith("win"):
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _creation_flags() -> int:
    if sys.platform.startswith("win"):
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _hf_headers(token: str = "") -> dict[str, str]:
    headers = {"User-Agent": "KothaBondhu/2.2", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _read_wav_float(path: Path) -> np.ndarray:
    """Read a WAV as mono float32, using libsndfile when available."""
    try:
        import soundfile as sf

        data, _ = sf.read(str(path), dtype="float32", always_2d=True)
        return np.asarray(data.mean(axis=1), dtype=np.float32)
    except Exception:  # pragma: no cover — stdlib fallback for plain PCM
        with wave.open(str(path), "rb") as handle:
            raw = handle.readframes(handle.getnframes())
            pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            if handle.getnchannels() > 1:
                pcm = pcm.reshape(-1, handle.getnchannels()).mean(axis=1)
            return pcm


def _write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())


class CloneTask:
    """A replayable event log for one setup/download operation."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.status = "running"
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


# --------------------------------------------------------------------------- manager


class CloneManager:
    """Owns optional clone data, setup tasks and the single clone engine."""

    def __init__(self) -> None:
        self.root = Path(cache_dir()) / "cloning"
        self.model_dir = self.root / "indicf5"
        self.vocoder_dir = self.root / "vocos-mel-24khz"
        self.voices_dir = self.root / "voices"
        self.venv_dir = self.root / "venv"
        for path in (self.root, self.model_dir, self.vocoder_dir, self.voices_dir):
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        self.token = ""
        self._tasks: dict[str, CloneTask] = {}
        self._tasks_lock = threading.Lock()
        self._device_cache: dict | None = None
        self._engine: CloneEngine | None = None
        self._engine_lock = threading.Lock()

    # --------------------------------------------------------------- environment

    @property
    def python(self) -> Path:
        return _venv_python(self.venv_dir)

    def environment_status(self) -> dict:
        if MOCK:
            return {
                "ready": True,
                "mock": True,
                "profile": "mock",
                "python": sys.executable,
                "message": "Test mode: the standard-library mock worker is active.",
            }
        marker = self.venv_dir / ".ready.json"
        ready = self.python.is_file() and marker.is_file()
        profile = ""
        if ready:
            try:
                profile = str(json.loads(marker.read_text(encoding="utf-8")).get("profile") or "")
            except (OSError, ValueError):
                ready = False
        return {
            "ready": ready,
            "mock": False,
            "profile": profile,
            "python": str(self.python),
            "message": "Isolated environment installed." if ready else "Not installed yet.",
        }

    def device_info(self) -> dict:
        if self._device_cache is not None:
            return self._device_cache
        if MOCK:
            self._device_cache = {
                "cuda": False,
                "name": "Mock CPU",
                "recommended": "mock",
                "warning": "Test mode — no real inference is performed.",
            }
            return self._device_cache
        name = ""
        try:
            probe = shutil.which("nvidia-smi")
            if probe:
                result = subprocess.run(
                    [probe, "--query-gpu=name", "--format=csv,noheader"],
                    capture_output=True,
                    text=True,
                    timeout=4,
                    creationflags=_creation_flags(),
                )
                if result.returncode == 0:
                    name = next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")
        except (OSError, subprocess.SubprocessError):
            name = ""
        cuda = bool(name)
        self._device_cache = {
            "cuda": cuda,
            "name": name or "CPU only",
            "recommended": "cuda" if cuda else "cpu",
            "warning": "" if cuda else CPU_WARNING,
        }
        return self._device_cache

    # --------------------------------------------------------------------- model

    def _sizes(self) -> dict[str, int]:
        path = self.model_dir.parent / "clone-sizes.json"
        sizes = dict(KNOWN_SIZES)
        if path.is_file():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                for key, value in (saved.get("files") or {}).items():
                    sizes[str(key)] = int(value)
            except (OSError, ValueError, TypeError):
                pass
        return sizes

    def model_status(self) -> dict:
        if MOCK:
            return {
                "ready": True,
                "bytes": 0,
                "cached_bytes": 0,
                "missing": [],
                "files": len(MODEL_FILES) + len(VOCODER_FILES),
                "model_dir": str(self.model_dir),
                "repo": REPO_ID,
                "mock": True,
            }
        sizes = self._sizes()
        missing: list[str] = []
        cached = 0
        entries: list[tuple[str, Path]] = []
        for name in MODEL_FILES:
            entries.append((name, self.model_dir / name))
        for name in VOCODER_FILES:
            entries.append((name, self.vocoder_dir / name))
        for name, path in entries:
            try:
                ok = path.is_file() and path.stat().st_size > 0
                if ok:
                    cached += path.stat().st_size
            except OSError:
                ok = False
            if not ok:
                missing.append(name)
        expected = sum(sizes.get(name, 0) for name, _ in entries) or cached
        return {
            "ready": not missing,
            "bytes": expected,
            "cached_bytes": cached,
            "missing": missing,
            "files": len(entries),
            "model_dir": str(self.model_dir),
            "repo": REPO_ID,
        }

    # -------------------------------------------------------------------- status

    def voices(self) -> list[dict]:
        result: list[dict] = []
        try:
            children = sorted(self.voices_dir.iterdir(), key=lambda p: p.name)
        except OSError:
            return result
        for child in children:
            meta_path = child / "meta.json"
            wav_path = child / "reference.wav"
            if not meta_path.is_file() or not wav_path.is_file():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(meta, dict):
                continue
            meta["id"] = str(meta.get("id") or child.name)
            meta["seconds"] = round(float(meta.get("seconds") or 0), 2)
            result.append(meta)
        result.sort(key=lambda item: float(item.get("created") or 0), reverse=True)
        return result

    def status(self) -> dict:
        environment = self.environment_status()
        model = self.model_status()
        device = self.device_info()
        voices = self.voices()
        installed = environment["ready"] and model["ready"]
        return {
            "ok": True,
            "available": True,
            "mock": MOCK,
            "repo": REPO_ID,
            "environment": environment,
            "model": model,
            "device": device,
            "voices": voices,
            "ready": installed,
            "can_synthesize": bool(installed and voices),
            "warning": CPU_WARNING if not device["cuda"] and not MOCK else "",
        }

    # --------------------------------------------------------------------- tasks

    def _task(self, key: str) -> tuple[CloneTask, bool]:
        with self._tasks_lock:
            task = self._tasks.get(key)
            fresh = task is None or task.status != "running"
            if fresh:
                task = CloneTask(key)
                self._tasks[key] = task
            return task, fresh

    @staticmethod
    def _stream(task: CloneTask) -> Iterator[dict]:
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

    # --------------------------------------------------------------------- setup

    def setup_stream(self, profile: str = "cpu") -> Iterator[dict]:
        profile = "cuda" if str(profile).lower() in {"cuda", "gpu"} else "cpu"
        if MOCK:
            yield {"type": "start", "profile": "mock", "title": "Mock clone environment"}
            yield {"type": "log", "stage": "setup", "line": "Mock mode — environment already available."}
            yield {"type": "done", "ready": True, "message": "Mock clone worker is ready."}
            return
        if self.environment_status()["ready"]:
            yield {"type": "done", "ready": True, "message": "Clone environment is already installed."}
            return
        task, fresh = self._task(f"setup:{profile}")
        if fresh:
            threading.Thread(
                target=self._setup_worker,
                args=(profile, task),
                name=f"clone-setup-{profile}",
                daemon=True,
            ).start()
        yield from self._stream(task)

    def _setup_worker(self, profile: str, task: CloneTask) -> None:
        task.emit(
            {
                "type": "start",
                "profile": profile,
                "title": "Voice-cloning environment",
                "message": "Installing an isolated Python environment for IndicF5.",
            }
        )
        try:
            if not self.python.is_file():
                self._run_command(
                    [sys.executable, "-m", "venv", str(self.venv_dir)],
                    task,
                    "create environment",
                )
            python = self.python
            self._run_command(
                [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--upgrade", "pip"],
                task,
                "upgrade pip",
            )
            if profile == "cuda":
                task.emit({"type": "log", "stage": "torch", "line": "Using PyTorch CUDA 12.1 wheels."})
                self._run_command(
                    [
                        str(python),
                        "-m",
                        "pip",
                        "install",
                        "--disable-pip-version-check",
                        "--no-input",
                        "--index-url",
                        "https://download.pytorch.org/whl/cu121",
                        "torch>=2.0",
                        "torchaudio>=2.0",
                    ],
                    task,
                    "install PyTorch (CUDA)",
                )
            else:
                self._run_command(
                    [
                        str(python),
                        "-m",
                        "pip",
                        "install",
                        "--disable-pip-version-check",
                        "--no-input",
                        "torch>=2.0",
                        "torchaudio>=2.0",
                    ],
                    task,
                    "install PyTorch (CPU)",
                )
            self._run_command(
                [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", *PIP_EXTRA],
                task,
                "install inference packages",
            )
            self.venv_dir.mkdir(parents=True, exist_ok=True)
            marker = self.venv_dir / ".ready.json"
            marker.write_text(
                json.dumps(
                    {"profile": profile, "created": time.time(), "python": sys.version.split()[0]},
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001 — report through SSE
            task.finish("error", {"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            return
        task.finish(
            "done",
            {
                "type": "done",
                "ready": True,
                "message": f"Clone environment installed ({profile.upper()}). Next: download the model.",
            },
        )

    @staticmethod
    def _run_command(cmd: list[str], task: CloneTask, label: str) -> None:
        shown = " ".join(cmd)
        task.emit({"type": "log", "stage": label, "line": f"$ {shown}"})
        env = dict(os.environ)
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        env["PYTHONNOUSERSITE"] = "1"
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=_creation_flags(),
            )
        except OSError as exc:
            raise RuntimeError(f"Could not start {label}: {exc}") from exc
        assert proc.stdout is not None
        for line in proc.stdout:
            cleaned = line.rstrip()[:500]
            if cleaned:
                task.emit({"type": "log", "stage": label, "line": cleaned})
        code = proc.wait()
        if code != 0:
            raise RuntimeError(f"{label} failed (exit code {code}). See the log above.")

    # ------------------------------------------------------------------ download

    def _file_plan(self, token: str) -> list[tuple[str, int, str, Path]]:
        plan: list[tuple[str, int, str, Path]] = []
        sizes: dict[str, int] = {}
        try:
            for repo in (REPO_ID, VOCODER_REPO):
                request = urllib.request.Request(HF_API.format(repo=repo), headers=_hf_headers(token))
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                for item in payload.get("siblings") or []:
                    name = str(item.get("rfilename") or "")
                    size = item.get("size") or (item.get("lfs") or {}).get("size") or 0
                    sizes[f"{repo}::{name}"] = int(size)
        except (OSError, ValueError, urllib.error.URLError):
            pass  # fall back to known sizes; the download itself still verifies bytes

        for name in MODEL_FILES:
            size = sizes.get(f"{REPO_ID}::{name}") or self._sizes().get(name, 0)
            plan.append((name, int(size), REPO_ID, self.model_dir))
        for name in VOCODER_FILES:
            size = sizes.get(f"{VOCODER_REPO}::{name}") or self._sizes().get(name, 0)
            plan.append((name, int(size), VOCODER_REPO, self.vocoder_dir))
        return plan

    def download_stream(self, token: str = "") -> Iterator[dict]:
        token = str(token or self.token or "").strip()[:512]
        if MOCK:
            yield {"type": "start", "title": "Mock IndicF5 model", "bytes": 0}
            yield {"type": "log", "line": "Mock mode — no files are downloaded."}
            yield {"type": "done", "ready": True, "message": "Mock model is ready."}
            return
        if self.model_status()["ready"]:
            yield {"type": "done", "ready": True, "message": "IndicF5 is already downloaded."}
            return
        if not token:
            yield {
                "type": "error",
                "message": "Add a Hugging Face token first — the official IndicF5 repository is gated.",
            }
            return
        task, fresh = self._task("download")
        if fresh:
            threading.Thread(
                target=self._download_worker,
                args=(token, task),
                name="clone-download",
                daemon=True,
            ).start()
        yield from self._stream(task)

    def _download_worker(self, token: str, task: CloneTask) -> None:
        try:
            plan = self._file_plan(token)
            total = sum(size for _, size, _, _ in plan)
            done = [0]
            task.emit(
                {
                    "type": "start",
                    "title": "IndicF5 voice model",
                    "bytes": total,
                    "repo": REPO_ID,
                    "message": "Downloading to the local model cache.",
                }
            )
            saved_sizes: dict[str, int] = {}
            for name, size, repo, base in plan:
                target = base / name
                target.parent.mkdir(parents=True, exist_ok=True)
                part = target.with_name(target.name + ".part")
                if target.is_file() and (not size or target.stat().st_size == size):
                    done[0] += target.stat().st_size
                    saved_sizes[name] = target.stat().st_size
                    task.emit(
                        {
                            "type": "progress",
                            "done": done[0],
                            "total": total,
                            "percent": round(100.0 * done[0] / total, 1) if total else None,
                            "label": f"{name} (cached)",
                        }
                    )
                    continue
                self._download_one(name, size, repo, token, target, part, total, done, task)
                saved_sizes[name] = target.stat().st_size
            # Small local stubs keep inference from importing the upstream
            # trainer (and its extra dependencies) just to reach DiT and CFM.
            f5 = self.model_dir / "f5_tts"
            for sub in ("", "infer", "model", "model/backbones"):
                directory = f5 / sub
                directory.mkdir(parents=True, exist_ok=True)
                init = directory / "__init__.py"
                if not init.exists():
                    init.write_text("", encoding="utf-8")
            # Only DiT is downloaded, so the upstream __init__ (which also
            # imports the trainer and MMDiT/UNetT) is replaced with this stub.
            (f5 / "model" / "__init__.py").write_text(
                "from f5_tts.model.cfm import CFM\n"
                "from f5_tts.model.backbones.dit import DiT\n"
                '__all__ = ["CFM", "DiT"]\n',
                encoding="utf-8",
            )
            manifest = self.root / "clone-sizes.json"
            manifest.write_text(
                json.dumps({"repo": REPO_ID, "files": saved_sizes, "total": done[0], "created": time.time()}, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001 — report through SSE
            task.finish("error", {"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            return
        task.finish(
            "done",
            {
                "type": "done",
                "ready": self.model_status()["ready"],
                "message": "IndicF5 downloaded. Add a reference voice to finish setup.",
            },
        )

    @staticmethod
    def _download_one(
        name: str,
        size: int,
        repo: str,
        token: str,
        target: Path,
        part: Path,
        total: int,
        done: list[int],
        task: CloneTask,
    ) -> None:
        existing = part.stat().st_size if part.is_file() else 0
        resume = bool(existing and size and existing < size)
        headers = _hf_headers(token)
        headers["Accept"] = "application/octet-stream"
        if resume:
            headers["Range"] = f"bytes={existing}-"
        url = HF_RESOLVE.format(repo=repo, filename=name)
        request = urllib.request.Request(url, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=60)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and part.is_file():  # range no longer valid
                part.unlink(missing_ok=True)
                return CloneManager._download_one(name, size, repo, token, target, part, total, done, task)
            raise RuntimeError(f"Could not download {name}: HTTP {exc.code}") from exc
        mode = "ab"
        with response:
            if resume and response.status != 206:
                part.unlink(missing_ok=True)
                existing = 0
                mode = "wb"
            if not resume or mode == "wb":
                existing = 0
            with open(part, mode) as handle:
                last = 0.0
                while True:
                    chunk = response.read(262_144)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done[0] += len(chunk)
                    now = time.monotonic()
                    if now - last >= 0.15:
                        task.emit(
                            {
                                "type": "progress",
                                "done": done[0],
                                "total": total,
                                "percent": round(100.0 * done[0] / total, 1) if total else None,
                                "label": name,
                            }
                        )
                        last = now
        actual = part.stat().st_size
        if size and actual != size:
            raise RuntimeError(f"Incomplete download: {name} ({actual} of {size} bytes). Retry to resume.")
        os.replace(part, target)
        task.emit(
            {
                "type": "log",
                "line": f"Saved {name} ({actual / 1e6:.1f} MB)",
            }
        )

    # -------------------------------------------------------------------- voices

    def resolve_voice(self, voice_id: str) -> tuple[dict, Path]:
        voice_id = _clean_id(voice_id)
        directory = self.voices_dir / voice_id
        meta_path = directory / "meta.json"
        wav_path = directory / "reference.wav"
        if not meta_path.is_file() or not wav_path.is_file():
            raise KeyError("That cloned voice was not found.")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            raise KeyError("That cloned voice is unreadable.")
        return meta, wav_path

    def voice_audio(self, voice_id: str) -> bytes:
        _, wav_path = self.resolve_voice(voice_id)
        return wav_path.read_bytes()

    def delete_voice(self, voice_id: str) -> bool:
        voice_id = _clean_id(voice_id)
        target = self.voices_dir / voice_id
        if not target.is_dir():
            return False
        shutil.rmtree(target, ignore_errors=True)
        return True

    def add_voice(self, payload: dict) -> dict:
        name = re.sub(r"\s+", " ", str(payload.get("name") or "")).strip()[:60]
        text = str(payload.get("text") or "").strip()[:600]
        if not name:
            raise ValueError("Give this voice a name.")
        if not text:
            raise ValueError("Type exactly what the reference clip says.")
        if len(text) < 8:
            raise ValueError("The reference transcript is too short (8 character minimum).")
        raw = payload.get("audio")
        if isinstance(raw, str):
            encoded = raw.split(",", 1)[1] if raw.startswith("data:") and "," in raw else raw
            try:
                audio_bytes = base64.b64decode(encoded, validate=False)
            except (ValueError, TypeError) as exc:
                raise ValueError("The audio upload could not be decoded.") from exc
        elif isinstance(payload.get("audio_bytes"), list):
            try:
                audio_bytes = bytes(int(value) & 0xFF for value in payload["audio_bytes"])
            except (TypeError, ValueError) as exc:
                raise ValueError("The audio upload could not be decoded.") from exc
        else:
            raise ValueError("Attach a reference audio clip.")
        if len(audio_bytes) < 1_024 or len(audio_bytes) > 12 * 1024 * 1024:
            raise ValueError("Reference audio must be between 1 KB and 12 MB.")

        audio, sample_rate = self._decode_reference(audio_bytes)
        if sample_rate != 24_000:
            audio = dsp.resample(audio, sample_rate, 24_000)
        audio = dsp.trim_silence(audio)
        seconds = float(audio.size) / 24_000.0
        if seconds < 3.0:
            raise ValueError(f"Reference clip is too short ({seconds:.1f}s). Use at least 3 seconds.")
        if seconds > 60.0:
            raise ValueError(f"Reference clip is too long ({seconds:.1f}s). Use at most 60 seconds.")
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 0:
            audio = audio * (0.92 / peak)

        voice_id = uuid.uuid4().hex[:10]
        directory = self.voices_dir / voice_id
        directory.mkdir(parents=True, exist_ok=True)
        wav_path = directory / "reference.wav"
        _write_wav(wav_path, audio, 24_000)
        meta = {
            "id": voice_id,
            "name": name,
            "text": text,
            "language": str(payload.get("language") or "bn")[:16] or "bn",
            "seconds": round(seconds, 2),
            "created": time.time(),
            "bytes": wav_path.stat().st_size,
        }
        (directory / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta

    @staticmethod
    def _decode_reference(audio_bytes: bytes) -> tuple[np.ndarray, int]:
        try:
            import soundfile as sf

            data, sample_rate = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=True)
            return np.asarray(data.mean(axis=1), dtype=np.float32), int(sample_rate)
        except Exception:
            try:
                with wave.open(io.BytesIO(audio_bytes), "rb") as handle:
                    frames = handle.readframes(handle.getnframes())
                    pcm = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
                    if handle.getnchannels() > 1:
                        pcm = pcm.reshape(-1, handle.getnchannels()).mean(axis=1)
                    return pcm, int(handle.getframerate())
            except Exception as exc:  # pragma: no cover
                raise ValueError("Unsupported audio format. Use WAV, FLAC or OGG.") from exc

    # -------------------------------------------------------------------- engine

    @property
    def engine(self) -> "CloneEngine":
        with self._engine_lock:
            if self._engine is None:
                self._engine = CloneEngine(self)
            return self._engine


# --------------------------------------------------------------------------- engine


class CloneEngine(TTSEngine):
    """A lazy TTSEngine backed by the isolated IndicF5 worker subprocess."""

    name = "IndicF5 clone"
    language = "bn"
    sample_rate = 24_000
    default_voice = ""

    def __init__(self, manager: CloneManager) -> None:
        super().__init__()
        self.manager = manager
        self._proc: subprocess.Popen | None = None
        self._ready = False
        self._stderr: deque[str] = deque(maxlen=100)
        self._stderr_thread: threading.Thread | None = None
        self._lock = threading.RLock()

    @property
    def is_loaded(self) -> bool:
        proc = self._proc
        return bool(self._ready and proc is not None and proc.poll() is None)

    def _tail(self) -> str:
        return "\n".join(self._stderr)[-1_500:]

    def load(self, progress=None) -> None:  # noqa: ARG002 — worker emits its own logs
        with self._lock:
            if self.is_loaded:
                return
            if not self.manager.status()["can_synthesize"]:
                raise ModelNotAvailable(
                    "Voice cloning is not set up yet. Open Models → Clone to install it and add a voice."
                )
            if MOCK:
                python = sys.executable
                env = dict(os.environ)
                env["AWAZ_CLONE_MOCK"] = "1"
            else:
                python = self.manager.python
                env = dict(os.environ)
                env["HF_HUB_OFFLINE"] = "1"
                env["TRANSFORMERS_OFFLINE"] = "1"
                env["TOKENIZERS_PARALLELISM"] = "false"
                env["PYTHONNOUSERSITE"] = "1"
            try:
                self._proc = subprocess.Popen(
                    [str(python), str(WORKER)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    bufsize=1,
                    env=env,
                    cwd=str(self.manager.root),
                    creationflags=_creation_flags(),
                )
            except OSError as exc:
                raise EngineError(f"Could not start the clone worker: {exc}") from exc
            assert self._proc.stdout is not None and self._proc.stderr is not None
            self._stderr.clear()
            self._stderr_thread = threading.Thread(
                target=self._pump_stderr, args=(self._proc,), name="clone-worker-stderr", daemon=True
            )
            self._stderr_thread.start()
            response = self._talk(
                {
                    "cmd": "load",
                    "model_dir": str(self.manager.model_dir),
                    "vocoder_dir": str(self.manager.vocoder_dir),
                    "device": self.manager.device_info()["recommended"],
                    "mock": MOCK,
                },
                timeout=900,
            )
            if response.get("type") == "error":
                message = str(response.get("message") or "IndicF5 could not be loaded.")
                self.cancel()
                raise EngineError(message)
            self._ready = True

    def _pump_stderr(self, proc: subprocess.Popen) -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            self._stderr.append(line.rstrip()[:500])

    def _talk(self, payload: dict, timeout: float | None = None) -> dict:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise EngineError("The clone worker is not running.")
        request_id = uuid.uuid4().hex[:8]
        message = dict(payload)
        message["id"] = request_id
        try:
            proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise EngineError(f"Clone worker stopped: {self._tail() or exc}") from exc

        done = threading.Event()
        deadline = time.monotonic() + timeout if timeout else None

        def watchdog() -> None:
            while deadline is not None and not done.is_set():
                if time.monotonic() >= deadline:
                    try:
                        proc.kill()
                    except OSError:
                        pass
                    return
                time.sleep(0.25)

        if deadline is not None:
            threading.Thread(target=watchdog, name="clone-worker-watchdog", daemon=True).start()
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    raise EngineError(f"Clone worker exited unexpectedly. {self._tail()}".strip())
                try:
                    response = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(response, dict) or response.get("id") != request_id:
                    continue
                if response.get("type") == "log":
                    continue
                return response
        finally:
            done.set()

    def synthesize(
        self, text: str, voice: str | None = None, speed: float = 1.0
    ) -> np.ndarray:
        with self._lock:
            if not self.is_loaded:
                self.load()
            meta, reference = self.manager.resolve_voice(voice or "")
            output = Path(tempfile.gettempdir()) / f"kotha-bondhu-clone-{uuid.uuid4().hex}.wav"
            payload = {
                "cmd": "synthesize",
                "text": text,
                "ref_audio_path": str(reference),
                "ref_text": str(meta.get("text") or ""),
                "speed": float(speed or 1.0),
                "output": str(output),
            }
            try:
                response = self._talk(payload)
            except Exception:
                output.unlink(missing_ok=True)
                raise
            if response.get("type") == "error":
                output.unlink(missing_ok=True)
                raise EngineError(str(response.get("message") or "Cloning failed."))
            if response.get("type") != "audio" or not output.is_file():
                output.unlink(missing_ok=True)
                raise EngineError("The clone worker did not return audio.")
            try:
                audio = _read_wav_float(output)
            finally:
                output.unlink(missing_ok=True)
            return np.asarray(audio, dtype=np.float32)

    def voices(self) -> list[str]:
        return [item["id"] for item in self.manager.voices()]

    def voice_label(self, voice: str) -> str:
        for item in self.manager.voices():
            if item.get("id") == voice:
                return f"{item.get('name')} · cloned Bengali"
        return voice

    def describe(self) -> dict:
        details = super().describe()
        details["loaded"] = self.is_loaded
        details["device"] = self.manager.device_info()["name"]
        return details

    def cancel(self) -> None:
        """Stop the worker immediately; synthesis cannot be interrupted in-process."""
        with self._lock:
            proc = self._proc
            self._ready = False
            self._loaded = False
            self._proc = None
            if proc is None:
                return
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                pass

    def unload(self) -> None:
        proc = self._proc
        if proc is not None and proc.stdin is not None and proc.poll() is None:
            try:
                proc.stdin.write('{"cmd":"shutdown","id":"shutdown"}\n')
                proc.stdin.flush()
            except (OSError, ValueError):
                pass
        self.cancel()


__all__ = [
    "CloneEngine",
    "CloneManager",
    "CloneRequest",
    "CloneTask",
    "CPU_WARNING",
    "MOCK",
    "REPO_ID",
]
