"""Paths and constants shared by the studio and its engines."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_cache_dir

from . import APP_NAME

#: Every engine's output is converted to this rate before it reaches the browser.
OUTPUT_SAMPLE_RATE = 24_000

#: Text planning budget: long enough to sound natural, short enough to stream.
CHUNK_MAX_CHARS = 240
CHUNK_MIN_CHARS = 28

ENGINE_NAME = {"en": "kokoro", "bn": "mms"}
LANGUAGE_NAMES = {"en": "English", "bn": "Bangla"}

# Kept for compatibility with paths.py, which points this at models/ at startup.
ENV_VAR = "AWAZ_CACHE_DIR"


def cache_dir() -> Path:
    """Return (and create) the folder that holds model files.

    ``AWAZ_CACHE_DIR`` is read on every call, so ``paths.configure_model_cache``
    can redirect the cache without any module reload.
    """
    override = os.environ.get(ENV_VAR)
    base = Path(override) if override else Path(user_cache_dir(APP_NAME, appauthor=False))
    base.mkdir(parents=True, exist_ok=True)
    return base
