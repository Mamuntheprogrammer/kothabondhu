"""Engine interface, registry and the two built-in ONNX backends."""

from .base import (
    EmptyText,
    EngineError,
    ModelNotAvailable,
    Progress,
    TTSEngine,
    UnsupportedLanguage,
)
from . import registry

# Importing these modules registers the default English and Bangla engines.
from . import kokoro as kokoro  # noqa: F401
from . import mms as mms  # noqa: F401

__all__ = [
    "EmptyText",
    "EngineError",
    "ModelNotAvailable",
    "Progress",
    "TTSEngine",
    "UnsupportedLanguage",
    "registry",
    "kokoro",
    "mms",
]
