"""Write synthesized audio to disk (WAV always; others when supported)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

WAV_SUFFIXES = {".wav"}
FLAC_SUFFIXES = {".flac"}
MP3_SUFFIXES = {".mp3"}
OGG_SUFFIXES = {".ogg", ".oga"}


class ExportError(RuntimeError):
    """The requested format cannot be written on this machine."""


def concat(chunks: list[np.ndarray]) -> np.ndarray:
    """Flatten and join non-empty audio chunks."""
    parts = [
        np.asarray(chunk, dtype=np.float32).reshape(-1)
        for chunk in chunks
        if np.asarray(chunk).size
    ]
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


def _formats() -> set[str]:
    import soundfile as sf

    return {name.lower() for name in sf.available_formats()}


def write_wav(path: Path | str, audio: np.ndarray, sample_rate: int) -> Path:
    """Write 16-bit PCM mono WAV."""
    import soundfile as sf

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.asarray(audio, dtype=np.float32).reshape(-1), int(sample_rate), subtype="PCM_16")
    return path


def _export_mp3(path: Path, audio: np.ndarray, sample_rate: int) -> Path:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    if "mp3" in _formats():
        sf.write(str(path), audio, int(sample_rate), format="MP3")
        return path

    try:
        from pydub import AudioSegment
    except ImportError as exc:
        raise ExportError(
            "MP3 needs a libsndfile with MP3 support, or `pip install pydub` "
            "plus ffmpeg on PATH. Export WAV instead."
        ) from exc

    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    segment = AudioSegment(data=pcm, sample_width=2, frame_rate=int(sample_rate), channels=1)
    try:
        segment.export(str(path), format="mp3")
    except Exception as exc:
        raise ExportError(f"ffmpeg is required for MP3 export ({exc}); export WAV instead") from exc
    return path


def export(path: Path | str, chunks: list[np.ndarray], sample_rate: int) -> Path:
    """Write *chunks* to *path*; the file extension selects the format."""
    path = Path(path)
    suffix = path.suffix.lower()
    audio = concat(chunks)

    if suffix in WAV_SUFFIXES or suffix == "":
        return write_wav(path if suffix else path.with_suffix(".wav"), audio, sample_rate)

    if suffix in FLAC_SUFFIXES:
        if "flac" not in _formats():
            raise ExportError("this build of soundfile cannot write FLAC")
        import soundfile as sf

        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(path), audio, int(sample_rate))
        return path

    if suffix in MP3_SUFFIXES:
        return _export_mp3(path, audio, sample_rate)

    if suffix in OGG_SUFFIXES:
        if "ogg" not in _formats():
            raise ExportError("this build of soundfile cannot write OGG")
        import soundfile as sf

        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(path), audio, int(sample_rate), format="OGG")
        return path

    raise ExportError(f"unsupported output format: {suffix or 'unknown'}")
