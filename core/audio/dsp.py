"""NumPy-only audio helpers: resampling, speed, loudness and silence trimming."""

from __future__ import annotations

import numpy as np

# Width of the windowed-sinc interpolation kernel used for rate conversion.
_RADIUS = 16
_WINDOW = np.hanning(2 * _RADIUS + 1)


def resample(audio: np.ndarray, src_rate: float, dst_rate: float) -> np.ndarray:
    """Convert between sample rates with a small band-limited interpolator.

    Rates may be integers or floats. The operation is linear-phase and keeps
    the output length proportional to ``dst_rate / src_rate``.
    """
    data = np.asarray(audio, dtype=np.float32).reshape(-1)
    if (
        data.size == 0
        or float(src_rate) <= 0
        or float(dst_rate) <= 0
        or float(src_rate) == float(dst_rate)
    ):
        return data.copy()

    ratio = float(src_rate) / float(dst_rate)
    count = max(1, int(round(data.size / ratio)))
    positions = np.arange(count, dtype=np.float64) * ratio
    base = np.floor(positions).astype(np.int64)

    total = np.zeros(count, dtype=np.float64)
    weight_sum = np.zeros(count, dtype=np.float64)
    for offset in range(-_RADIUS, _RADIUS + 1):
        index = base + offset
        valid = (index >= 0) & (index < data.size)
        if not valid.any():
            continue
        weight = _WINDOW[offset + _RADIUS] * np.sinc(positions - index)
        total[valid] += data[index[valid]] * weight[valid]
        weight_sum[valid] += weight[valid]

    scale = np.divide(
        1.0, weight_sum, out=np.zeros_like(weight_sum), where=np.abs(weight_sum) > 1e-12
    )
    return (total * scale).astype(np.float32)


def change_speed(audio: np.ndarray, speed: float) -> np.ndarray:
    """Play *audio* faster/slower; pitch shifts with speed (no phase vocoder)."""
    factor = float(speed)
    if abs(factor - 1.0) < 1e-3:
        return np.asarray(audio, dtype=np.float32).reshape(-1).copy()
    factor = min(max(factor, 0.5), 2.0)
    return resample(audio, 1.0, 1.0 / factor)


def rms(audio: np.ndarray) -> float:
    data = np.asarray(audio, dtype=np.float64).reshape(-1)
    return float(np.sqrt(np.mean(data * data))) if data.size else 0.0


def match_loudness(audio: np.ndarray, target_db: float = -20.0) -> np.ndarray:
    """Scale to a target RMS without pushing the peak past 0.99."""
    data = np.asarray(audio, dtype=np.float32).reshape(-1)
    current = rms(data)
    if current <= 0.0:
        return data.copy()
    gain = (10.0 ** (target_db / 20.0)) / current
    peak = float(np.max(np.abs(data))) * gain
    if peak > 0.99:
        gain *= 0.99 / peak
    return (data * gain).astype(np.float32)


def trim_silence(audio: np.ndarray, threshold_db: float = -45.0) -> np.ndarray:
    """Remove leading and trailing quiet samples."""
    data = np.asarray(audio, dtype=np.float32).reshape(-1)
    if data.size == 0:
        return data.copy()
    keep = np.flatnonzero(np.abs(data) > 10.0 ** (threshold_db / 20.0))
    if keep.size == 0:
        return data.copy()
    return data[keep[0] : keep[-1] + 1].copy()
