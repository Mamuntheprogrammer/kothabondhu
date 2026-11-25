"""Language-to-engine registry with lazy, cached instances."""

from __future__ import annotations

import threading
from typing import Callable

from .base import TTSEngine, UnsupportedLanguage

_FACTORIES: dict[str, Callable[[], TTSEngine]] = {}
_INSTANCES: dict[str, TTSEngine] = {}
_LOCK = threading.RLock()


def register(language: str, factory: Callable[[], TTSEngine]) -> None:
    """Register (or replace) the engine factory for a language."""
    with _LOCK:
        _FACTORIES[language] = factory
        stale = _INSTANCES.pop(language, None)
    if stale is not None:
        stale.unload()


def languages() -> list[str]:
    with _LOCK:
        return sorted(_FACTORIES)


def has_engine(language: str) -> bool:
    with _LOCK:
        return language in _FACTORIES


def get_engine(language: str) -> TTSEngine:
    """Return the cached engine for *language*, constructing it on first use."""
    with _LOCK:
        engine = _INSTANCES.get(language)
        if engine is not None:
            return engine
        factory = _FACTORIES.get(language)
        if factory is None:
            raise UnsupportedLanguage(f"No speech engine for language {language!r}.")
        engine = factory()
        _INSTANCES[language] = engine
        return engine


def unload_all() -> None:
    with _LOCK:
        engines = list(_INSTANCES.values())
        _INSTANCES.clear()
    for engine in engines:
        engine.unload()
