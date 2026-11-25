"""Local synthesis core for Kotha Bondhu (কথাবন্ধু).

This package contains the small amount of machinery the studio used to borrow
from the external ``awaaz`` project: configuration, DSP helpers, audio export,
the engine interface and registry, model bookkeeping, and the text pipeline.

Everything here is self-contained and uses only NumPy, ONNX Runtime, Kokoro and
soundfile (plus platformdirs for the cache path).
"""

from __future__ import annotations

__version__ = "2.2.0"
APP_NAME = "Kotha Bondhu"
