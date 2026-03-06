"""
Prompt loader utilities for EditBot.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Tuple

from core.logging import setup_logger

logger = setup_logger("prompt_loader")

PROJECT_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"

# filename -> (mtime, content)
_PROMPT_CACHE: Dict[str, Tuple[float, str]] = {}
_WARNED: Dict[str, bool] = {}


def load_prompt_text(filename: str, fallback: str = "") -> str:
    """
    Load a prompt from prompts/<filename>, with mtime-based caching.
    Returns fallback when file is missing/unreadable/empty.
    """
    path = PROMPTS_DIR / filename
    try:
        stat = path.stat()
        cached = _PROMPT_CACHE.get(filename)
        if cached and cached[0] == stat.st_mtime:
            return cached[1]

        text = path.read_text(encoding="utf-8").strip()
        if not text:
            if not _WARNED.get(filename):
                logger.warning(f"Prompt file is empty: {path}")
                _WARNED[filename] = True
            return fallback.strip()

        _PROMPT_CACHE[filename] = (stat.st_mtime, text)
        return text
    except Exception as exc:
        if not _WARNED.get(filename):
            logger.warning(f"Failed to load prompt file {path}: {exc}")
            _WARNED[filename] = True
        return fallback.strip()

