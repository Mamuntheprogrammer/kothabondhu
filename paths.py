"""Where the TTS models live.

Preference order:

1. ``--models PATH`` on the command line (or an existing ``AWAZ_CACHE_DIR``),
2. the bundled ``ai_version/models`` folder next to this file,
3. the normal per-user cache from the original engine.

Because the bundled folder is checked automatically, a copy of this project is
self-contained: move it to another machine and all three engines work with no
download, as long as ``ai_version/models`` was copied along with the code.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BUNDLED_MODELS = BASE_DIR / "models"
MODEL_KEYS = ("kokoro", "mms-ben-onnx", "indic-tiny-bn")

# `awaaz.config.cache_dir()` reads AWAZ_CACHE_DIR. Its docstring spells the
# variable with a double A, so we accept both and always set the real one.
ENV_VAR = "AWAZ_CACHE_DIR"
LEGACY_ENV_VAR = "AWAAZ_CACHE_DIR"


def has_any_model(path: Path) -> bool:
    """True when *path* looks like a model cache with at least one model."""
    try:
        if not path.is_dir():
            return False
        for key in MODEL_KEYS:
            model_dir = path / key
            if model_dir.is_dir() and any(model_dir.iterdir()):
                return True
    except OSError:
        return False
    return False


def bundled_models() -> Path | None:
    return BUNDLED_MODELS if has_any_model(BUNDLED_MODELS) else None


def configure_model_cache(explicit: str | os.PathLike[str] | None = None) -> Path | None:
    """Point the core at the right model folder and return it.

    ``cache_dir()`` in :mod:`awaaz.config` reads ``AWAAZ_CACHE_DIR`` on every
    call, so setting the variable here is enough — no core module needs to be
    reloaded. Returns ``None`` when the platform default should be used.
    """
    if explicit:
        path = Path(explicit).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        os.environ[ENV_VAR] = str(path)
        return path

    current = os.environ.get(ENV_VAR) or os.environ.get(LEGACY_ENV_VAR)
    if current:
        os.environ[ENV_VAR] = current
        return Path(current).expanduser()

    bundled = bundled_models()
    if bundled is not None:
        os.environ[ENV_VAR] = str(bundled)
        return bundled
    return None


def using_bundled() -> bool:
    """True when this run is served from the project's own ``models`` folder."""
    current = os.environ.get(ENV_VAR) or os.environ.get(LEGACY_ENV_VAR)
    if not current:
        return False
    try:
        return Path(current).resolve() == BUNDLED_MODELS.resolve()
    except OSError:
        return False
