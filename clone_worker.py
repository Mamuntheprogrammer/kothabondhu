#!/usr/bin/env python3
"""IndicF5 inference worker for Kotha Bondhu (কথাবন্ধু).

Runs inside ``<cache>/cloning/venv`` and speaks JSON Lines on stdin/stdout.
It is intentionally a separate process: the heavy PyTorch stack is imported
here, never in the web server. With ``AWAZ_CLONE_MOCK=1`` it uses only the
standard library, which lets the whole UI/job pipeline be tested offline.
"""

from __future__ import annotations

import json
import math
import os
import sys
import traceback
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 24_000
MOCK = os.environ.get("AWAZ_CLONE_MOCK") == "1"


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def reply(request_id: str, kind: str, **extra) -> None:
    send({"id": request_id, "type": kind, **extra})


def log(message: str, request_id: str = "") -> None:
    send({"id": request_id, "type": "log", "line": str(message)[:500]})


def _write_wav(path: Path, audio: np.ndarray) -> None:
    pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())


def _mock_audio(text: str) -> np.ndarray:
    """A short deterministic tone so tests can exercise streaming and export."""
    duration = min(4.0, max(0.9, 0.65 + len(text) / 42.0))
    count = int(duration * SAMPLE_RATE)
    t = np.arange(count, dtype=np.float32) / SAMPLE_RATE
    seed = sum(ord(char) for char in text) % 97
    base = 165.0 + seed
    envelope = np.minimum(1.0, t * 12.0) * np.exp(-2.2 * t / duration)
    audio = (
        0.34 * np.sin(2.0 * math.pi * base * t)
        + 0.14 * np.sin(2.0 * math.pi * base * 1.5 * t)
        + 0.06 * np.sin(2.0 * math.pi * base * 2.0 * t)
    )
    return np.asarray(audio * envelope, dtype=np.float32)


def _change_speed(audio: np.ndarray, speed: float) -> np.ndarray:
    speed = min(max(float(speed or 1.0), 0.5), 2.0)
    if abs(speed - 1.0) < 1e-3:
        return np.asarray(audio, dtype=np.float32)
    try:
        import librosa

        return np.asarray(
            librosa.effects.time_stretch(np.asarray(audio, dtype=np.float32), rate=speed),
            dtype=np.float32,
        )
    except Exception:
        # Crude fallback that keeps pitch shifting, but needs no extra package.
        source = np.asarray(audio, dtype=np.float32)
        count = max(1, int(source.size / speed))
        positions = np.linspace(0, source.size - 1, count)
        return np.interp(positions, np.arange(source.size), source).astype(np.float32)


class Worker:
    def __init__(self) -> None:
        self.model = None
        self.device = "cpu"
        self.ready = False
        self._real_patch = None

    # ------------------------------------------------------------------ loading

    def load(self, message: dict) -> dict:
        request_id = str(message.get("id") or "")
        if MOCK:
            self.ready = True
            self.device = "mock-cpu"
            log("Mock worker loaded (stdlib only).", request_id)
            return {"sample_rate": SAMPLE_RATE, "device": self.device}

        model_dir = Path(str(message.get("model_dir") or "")).expanduser().resolve()
        vocoder_dir = Path(str(message.get("vocoder_dir") or "")).expanduser().resolve()
        if not (model_dir / "model.safetensors").is_file():
            raise FileNotFoundError(f"IndicF5 weights are missing from {model_dir}")
        if not (vocoder_dir / "pytorch_model.bin").is_file():
            raise FileNotFoundError(f"Vocos vocoder is missing from {vocoder_dir}")

        log("Loading PyTorch and IndicF5…", request_id)
        if str(model_dir) not in sys.path:
            sys.path.insert(0, str(model_dir))
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

        import torch
        import huggingface_hub

        # The upstream model downloads its own weights and vocoder via
        # hf_hub_download. Everything is already local, so redirect those calls
        # to the setup folder and keep inference fully offline.
        def local_hub_download(repo_id=None, filename=None, **_kwargs):
            repo = str(repo_id or "")
            base = vocoder_dir if "vocos" in repo.lower() else model_dir
            path = base / str(filename or "")
            if not path.is_file():
                raise FileNotFoundError(f"Offline model file not found: {path}")
            return str(path)

        huggingface_hub.hf_hub_download = local_hub_download
        self._real_patch = local_hub_download
        # Compiling adds a long warm-up without helping a single short sentence.
        torch.compile = lambda module, *args, **kwargs: module  # type: ignore[assignment]

        from transformers import AutoModel

        preferred = str(message.get("device") or "auto")
        if preferred == "cuda" and torch.cuda.is_available():
            self.device = "cuda"
        elif torch.backends.mps.is_available():
            self.device = "mps"
        else:
            self.device = "cpu"

        log(f"Loading weights on {self.device}…", request_id)
        self.model = AutoModel.from_pretrained(
            str(model_dir), trust_remote_code=True, local_files_only=True
        )
        self.model.to(self.device)
        self.model.eval()
        self.ready = True
        log(f"IndicF5 ready on {self.device}.", request_id)
        return {"sample_rate": SAMPLE_RATE, "device": self.device}

    # -------------------------------------------------------------- synthesis

    def synthesize(self, message: dict) -> dict:
        request_id = str(message.get("id") or "")
        if not self.ready:
            raise RuntimeError("The clone model is not loaded.")
        text = str(message.get("text") or "").strip()
        if not text:
            raise ValueError("There is no text to synthesize.")
        reference = Path(str(message.get("ref_audio_path") or ""))
        if not reference.is_file():
            raise FileNotFoundError(f"Reference audio not found: {reference}")
        ref_text = str(message.get("ref_text") or "").strip()
        if not ref_text:
            raise ValueError("This voice is missing its reference transcript.")
        output = Path(str(message.get("output") or ""))
        output.parent.mkdir(parents=True, exist_ok=True)
        speed = float(message.get("speed") or 1.0)

        if MOCK:
            audio = _mock_audio(text)
        else:
            log(f"Cloning {len(text)} characters…", request_id)
            generated = self.model(text, ref_audio_path=str(reference), ref_text=ref_text)
            audio = np.asarray(generated, dtype=np.float32).reshape(-1)
            # Upstream returns int16 samples from pydub in some versions.
            if np.issubdtype(np.asarray(generated).dtype, np.integer):
                audio = np.asarray(generated, dtype=np.float32).reshape(-1) / 32768.0
            audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
            audio = _change_speed(audio, speed)
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            if peak > 1.0:
                audio = audio / peak

        _write_wav(output, audio)
        seconds = round(float(audio.size) / SAMPLE_RATE, 3)
        reply(request_id, "audio", path=str(output), seconds=seconds, device=self.device)
        return {"seconds": seconds}


def main() -> int:
    worker = Worker()
    send({"type": "hello", "mock": MOCK, "sample_rate": SAMPLE_RATE})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            send({"type": "error", "message": "Malformed JSON command."})
            continue
        if not isinstance(message, dict):
            continue
        command = str(message.get("cmd") or "")
        request_id = str(message.get("id") or "")
        try:
            if command == "load":
                details = worker.load(message)
                reply(request_id, "ready", **details)
            elif command == "synthesize":
                worker.synthesize(message)
            elif command == "ping":
                reply(request_id, "pong", ready=worker.ready, device=worker.device)
            elif command == "shutdown":
                reply(request_id, "shutdown")
                break
            else:
                reply(request_id, "error", message=f"Unknown command: {command}")
        except Exception as exc:  # noqa: BLE001 — keep the process alive
            detail = traceback.format_exc()[-2_000:]
            log(detail, request_id)
            reply(request_id, "error", message=f"{type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
