"""
Clarifier - detects conflicting time instructions and asks the LLM to resolve them.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.logging import setup_logger
from core.prompt_loader import load_prompt_text
from tools.silence_cutter_tool import parse_manual_cut_segments_from_prompt

logger = setup_logger("clarifier")
PROMPT_CLARIFIER_SYSTEM = "clarifier_system_prompt.txt"

TIME_TOKEN_PATTERN = r"(?:\d+(?::\d+){1,2}(?:\.\d+)?|\d+(?:\.\d+)?)(?:\s*(?:ms|s|sec|secs|seconds))?"
TIME_RANGE_REGEX = re.compile(
    rf"(?:from|between)\s*({TIME_TOKEN_PATTERN})\s*(?:to|and|through|-)\s*({TIME_TOKEN_PATTERN})",
    re.IGNORECASE
)
TIME_RANGE_FALLBACK_REGEX = re.compile(
    rf"({TIME_TOKEN_PATTERN})\s*(?:to|through|-)\s*({TIME_TOKEN_PATTERN})",
    re.IGNORECASE
)
STOCK_KEYWORDS = [
    "stock",
    "stock footage",
    "stock video",
    "stock clip",
    "b-roll",
    "b roll",
    "broll",
    "cutaway",
    "insert"
]

CUT_KEYWORDS = [
    "cut",
    "trim",
    "remove",
    "delete",
    "snip",
    "excise"
]


def _parse_timecode_to_seconds(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    text = re.sub(r"[a-z]+$", "", text).strip(" ,.;")
    if not text:
        return None
    if ":" in text:
        parts = text.split(":")
        if len(parts) not in (2, 3):
            return None
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        if len(nums) == 2:
            minutes, seconds = nums
            return minutes * 60.0 + seconds
        hours, minutes, seconds = nums
        return hours * 3600.0 + minutes * 60.0 + seconds
    try:
        return float(text)
    except ValueError:
        return None


def _find_time_ranges(text: str) -> List[Tuple[float, float]]:
    ranges: List[Tuple[float, float]] = []
    if not text:
        return ranges

    for match in TIME_RANGE_REGEX.finditer(text):
        start = _parse_timecode_to_seconds(match.group(1))
        end = _parse_timecode_to_seconds(match.group(2))
        if start is None or end is None:
            continue
        if end < start:
            start, end = end, start
        ranges.append((start, end))

    if ranges:
        return ranges

    for match in TIME_RANGE_FALLBACK_REGEX.finditer(text):
        start = _parse_timecode_to_seconds(match.group(1))
        end = _parse_timecode_to_seconds(match.group(2))
        if start is None or end is None:
            continue
        if end < start:
            start, end = end, start
        ranges.append((start, end))

    return ranges


def _split_clauses(text: str) -> List[str]:
    if not text:
        return []
    separators = (
        r"(?:\.\s+|;\s+|\n+"
        r"|,?\s+and\s+then\s+"
        r"|,?\s+then\s+"
        r"|,?\s+next\s+"
        r"|,?\s+after\s+that\s+"
        r"|,?\s+afterwards\s+"
        r"|,?\s+also\s+)"
    )
    parts = re.split(separators, text, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p and p.strip()]


def extract_stock_ranges(prompt: str) -> List[Dict[str, Any]]:
    if not prompt:
        return []
    prompt_lower = prompt.lower()
    if not any(k in prompt_lower for k in STOCK_KEYWORDS):
        return []

    clauses = _split_clauses(prompt)
    ranges: List[Dict[str, Any]] = []
    for clause in clauses:
        clause_lower = clause.lower()
        if not any(k in clause_lower for k in STOCK_KEYWORDS):
            continue
        for start, end in _find_time_ranges(clause):
            ranges.append({
                "start": start,
                "end": end,
                "source": clause
            })

    if not ranges:
        for start, end in _find_time_ranges(prompt):
            ranges.append({
                "start": start,
                "end": end,
                "source": "prompt"
            })

    return ranges


def extract_cut_ranges(prompt: str) -> List[Dict[str, Any]]:
    if not prompt:
        return []

    ranges: List[Dict[str, Any]] = []
    for seg in parse_manual_cut_segments_from_prompt(prompt):
        ranges.append({
            "start": seg["start"],
            "end": seg["end"],
            "source": "manual_cut_parser"
        })

    clauses = _split_clauses(prompt)
    for clause in clauses:
        clause_lower = clause.lower()
        if not any(k in clause_lower for k in CUT_KEYWORDS):
            continue
        for start, end in _find_time_ranges(clause):
            ranges.append({
                "start": start,
                "end": end,
                "source": clause
            })

    # Deduplicate
    unique = []
    seen = set()
    for seg in ranges:
        key = (round(seg["start"], 3), round(seg["end"], 3))
        if key in seen:
            continue
        seen.add(key)
        unique.append(seg)

    return unique


def extract_transition_duration(prompt: str) -> Optional[float]:
    if not prompt:
        return None
    prompt_lower = prompt.lower()
    if "transition" not in prompt_lower and "cross dissolve" not in prompt_lower and "crossfade" not in prompt_lower:
        return None
    range_match = re.search(
        r"(?:transition(?:\s*duration|\s*length)?|duration|length)\s*"
        r"(?:is|=|:|of|for|from)?\s*"
        r"(?:duration\s*)?"
        r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds)?\s*"
        r"(?:to|-|–|—)\s*"
        r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds)?",
        prompt_lower
    )
    if range_match:
        start_val = _parse_timecode_to_seconds(range_match.group(1))
        end_val = _parse_timecode_to_seconds(range_match.group(2))
        if start_val is not None and end_val is not None and end_val > start_val:
            return end_val - start_val

    match = re.search(
        r"(?:transition\s*duration|transition\s*length|transition)\s*(?:is|=|:|of|for)?\s*(\d+(?:\.\d+)?)\s*(s|sec|secs|seconds)?",
        prompt_lower
    )
    if not match:
        return None
    value = _parse_timecode_to_seconds(match.group(1))
    if value is None or value <= 0:
        return None
    return value


def _ranges_overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return max(a_start, b_start) < min(a_end, b_end)


def detect_time_conflicts(prompt: str) -> List[Dict[str, Any]]:
    conflicts: List[Dict[str, Any]] = []
    if not prompt:
        return conflicts

    cut_segments = extract_cut_ranges(prompt)
    stock_ranges = extract_stock_ranges(prompt)
    transition_duration = extract_transition_duration(prompt)

    for cut in cut_segments:
        for stock in stock_ranges:
            if _ranges_overlap(cut["start"], cut["end"], stock["start"], stock["end"]):
                conflicts.append({
                    "type": "cut_overlap_stock",
                    "cut": cut,
                    "stock": stock
                })

    if transition_duration is not None and stock_ranges:
        for stock in stock_ranges:
            stock_duration = stock["end"] - stock["start"]
            if stock_duration > 0 and transition_duration >= stock_duration:
                conflicts.append({
                    "type": "transition_too_long",
                    "transition_duration": transition_duration,
                    "stock_duration": stock_duration,
                    "stock": stock
                })

    return conflicts


def generate_clarification(prompt: str, conflicts: List[Dict[str, Any]], model_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not conflicts:
        return None
    if model_name is None:
        model_name = (
            os.getenv("EDITBOT_HEAD_MODEL")
            or os.getenv("EDITBOT_QA_MODEL")
            or os.getenv("EDITBOT_LLM_MODEL")
            or "deepseek-r1:1.5b"
        )

    try:
        import ollama
    except Exception as exc:
        logger.error(f"Ollama not available for clarifications: {exc}")
        return None

    reasoning_mode = (os.getenv("EDITBOT_REASONING") or os.getenv("OLLAMA_REASONING") or "off").lower()
    reasoning_guard = "Do not include chain-of-thought. Respond with final answers only." if reasoning_mode == "off" else ""

    system_prompt = load_prompt_text(
        PROMPT_CLARIFIER_SYSTEM,
        fallback=(
            "You are EditBot. Ask a single concise clarification question and return valid JSON "
            "with keys: question, options."
        ),
    )
    if reasoning_guard:
        system_prompt = f"{system_prompt} {reasoning_guard}"

    user_payload = {
        "prompt": prompt,
        "conflicts": conflicts
    }

    try:
        response = ollama.chat(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload)}
            ],
            format="json"
        )
        content = response["message"]["content"]
        data = json.loads(content)
        question = data.get("question")
        options = data.get("options") or []
        if not question:
            return None
        return {
            "question": question,
            "options": options
        }
    except Exception as exc:
        logger.error(f"Failed to generate clarification: {exc}")
        return None
