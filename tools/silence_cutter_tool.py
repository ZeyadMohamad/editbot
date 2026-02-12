"""
Silence cutter tool for removing dead air and filler words.
Uses waveform-based silence detection (dBFS) and optional lightweight speech
recognition for filler word detection (Whisper or Vosk).
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import wave
import audioop
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from core.logging import setup_logger
from tools.base_tool import BaseTool, ToolResult, register_tool

logger = setup_logger("silence_cutter_tool")

# -----------------------------
# Defaults and filler lexicons
# -----------------------------

DEFAULT_SETTINGS = {
    "threshold_db": -35.0,          # dBFS threshold for silence
    "min_silence_duration": 0.3,    # seconds
    "padding": 0.05,                # seconds to keep around cuts
    "chunk_ms": 30,                 # analysis window size
    "filler_detection": True,
    "filler_model_size": "small",
    "filler_language": "auto",
    "filler_confidence": 0.5,
    "filler_aggressive": False,
    "filler_engine": "whisper"      # whisper | vosk | auto
}

EN_FILLER_WORDS = {
    "um", "uh", "erm", "er", "ah", "hmm", "mm", "mmm"
}

EN_FILLER_WORDS_LOOSE = {
    "like", "so", "okay", "right"
}

EN_FILLER_PHRASES = {
    "you know",
    "i mean",
    "sort of",
    "kind of"
}

AR_FILLER_WORDS = {
    "اممم",
    "ممم",
    "مم",
    "إمم",
    "ااه",
    "اااه",
    "ااااه",
    "هممم",
    "اييه",
    "ايييه",
    "اييييه",
    "ايييييه",
    "اييييييه"
}

AR_FILLER_WORDS_LOOSE = {
    "طيب",
    "بس"
}

AR_FILLER_PHRASES = {
    "مش عارف",
    "ما ادري",
    "و اييه",
    "و ايييه"
}

AR_FILLER_TRANSLIT = {
    "yani", "yaani", "ya'ni", "yaani", "ya3ni"
}

SILENCE_KEYWORDS = [
    "silence", "silent", "remove silence", "cut silence", "trim silence",
    "remove pauses", "cut pauses", "trim pauses", "dead air", "tighten pacing",
    "remove filler", "filler words", "um", "uh", "you know",
    "حذف الصمت", "ازالة الصمت", "إزالة الصمت", "قص الصمت",
    "سكتات", "فواصل", "تمتمة", "يعني", "اممم", "ممم"
]

# Manual cut parsing
MANUAL_CUT_VERBS = ["cut", "trim", "remove", "delete", "snip", "excise"]
TIME_TOKEN_PATTERN = r"(?:\d+(?::\d+){1,2}(?:\.\d+)?|\d+(?:\.\d+)?)(?:\s*(?:ms|s|sec|secs|seconds))?"
CUT_RANGE_REGEX = re.compile(
    rf"(?:{'|'.join(MANUAL_CUT_VERBS)})\s*"
    rf"(?:out\s+)?(?:at\s+)?(?:duration\s+)?(?:from\s+|between\s+)?(?:duration\s+)?"
    rf"({TIME_TOKEN_PATTERN})\s*(?:to|until|through|-|and)\s*({TIME_TOKEN_PATTERN})",
    re.IGNORECASE
)


def should_apply_silence_cut(prompt: str) -> bool:
    """Heuristic: detect if user asked for silence/filler removal."""
    prompt_lower = (prompt or "").lower()
    for keyword in SILENCE_KEYWORDS:
        if " " in keyword:
            if keyword in prompt_lower:
                return True
            continue
        pattern = rf"(?<!\w){re.escape(keyword)}(?!\w)"
        if re.search(pattern, prompt_lower):
            return True
    return bool(parse_manual_cut_segments_from_prompt(prompt))


def parse_silence_settings_from_prompt(prompt: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Extract silence cutter settings from a natural language prompt."""
    settings = dict(DEFAULT_SETTINGS)
    settings.update(defaults or {})

    if not prompt:
        return settings

    prompt_lower = prompt.lower()

    # Threshold in dB
    db_match = re.search(r"(-?\d+(?:\.\d+)?)\s*db", prompt_lower)
    if db_match:
        val = float(db_match.group(1))
        settings["threshold_db"] = -abs(val) if val > 0 else val

    # Minimum silence duration (s or ms)
    min_silence_match = re.search(
        r"(?:min\s*silence|min\s*pause|silence\s*duration|pause\s*duration|silence)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|seconds)?",
        prompt_lower
    )
    if min_silence_match:
        val = float(min_silence_match.group(1))
        unit = min_silence_match.group(2) or "s"
        if unit.startswith("m"):
            val = val / 1000.0
        settings["min_silence_duration"] = val

    # Padding/margins
    padding_match = re.search(
        r"(?:padding|margin|micro\s*margin)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|seconds)?",
        prompt_lower
    )
    if padding_match:
        val = float(padding_match.group(1))
        unit = padding_match.group(2) or "s"
        if unit.startswith("m"):
            val = val / 1000.0
        settings["padding"] = val

    # Filler detection toggles
    if re.search(r"\b(no filler|ignore filler|keep filler)\b", prompt_lower):
        settings["filler_detection"] = False
    elif re.search(r"\b(remove filler|cut filler|filler words|um|uh|you know)\b", prompt_lower):
        settings["filler_detection"] = True

    # Aggressive filler detection
    if "aggressive" in prompt_lower and "filler" in prompt_lower:
        settings["filler_aggressive"] = True

    return settings


def _parse_timecode_to_seconds(value: str) -> Optional[float]:
    """Parse timecodes like 18.005, 1:02.5, 00:00:18.005 into seconds."""
    if value is None:
        return None
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


def parse_manual_cut_segments_from_prompt(prompt: str) -> List[Dict[str, float]]:
    """Extract manual cut ranges from prompt like 'cut from 18.005 to 18.670'."""
    if not prompt:
        return []

    segments: List[Dict[str, float]] = []
    for match in CUT_RANGE_REGEX.finditer(prompt):
        start_raw, end_raw = match.group(1), match.group(2)
        start = _parse_timecode_to_seconds(start_raw)
        end = _parse_timecode_to_seconds(end_raw)
        if start is None or end is None:
            continue
        if end < start:
            start, end = end, start
        if end == start:
            continue
        segments.append({"start": float(start), "end": float(end)})

    return segments


def _contains_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text or ""))


def _normalize_arabic(text: str) -> str:
    text = text or ""
    text = re.sub(r"[\u064B-\u065F\u0670\u06D6-\u06ED]", "", text)  # diacritics
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ى", "ي").replace("ؤ", "و").replace("ئ", "ي")
    text = text.replace("ة", "ه").replace("ـ", "")
    text = re.sub(r"[^\u0600-\u06FF]+", "", text)
    return text


def _normalize_english(text: str) -> str:
    text = text or ""
    text = text.lower()
    text = re.sub(r"[^a-z']", "", text)
    text = text.replace("'", "")
    text = re.sub(r"(.)\1{2,}", r"\1\1", text)
    return text


def _is_filler_token(token: str, aggressive: bool = False) -> bool:
    if not token:
        return False

    if _contains_arabic(token):
        norm = _normalize_arabic(token)
        if norm in AR_FILLER_WORDS:
            return True
        if aggressive and norm in AR_FILLER_WORDS_LOOSE:
            return True
        # Elongated Arabic filler noises (ممم, اه, اممم)
        if re.fullmatch(r"ا?م{2,}", norm):
            return True
        if re.fullmatch(r"ا?ه{2,}", norm):
            return True
        return False

    norm = _normalize_english(token)
    if norm in EN_FILLER_WORDS:
        return True
    if aggressive and norm in EN_FILLER_WORDS_LOOSE:
        return True
    if norm in AR_FILLER_TRANSLIT:
        return True
    # Elongated English filler noises
    if re.fullmatch(r"u+m+", norm):
        return True
    if re.fullmatch(r"u+h+", norm):
        return True
    if re.fullmatch(r"e+r+m+", norm):
        return True
    if re.fullmatch(r"e+r+", norm):
        return True
    if re.fullmatch(r"a+h+", norm):
        return True
    if re.fullmatch(r"m{2,}", norm):
        return True
    return False


def _tokenize_phrase(phrase: str) -> List[str]:
    parts = phrase.strip().split()
    return [_normalize_arabic(p) if _contains_arabic(p) else _normalize_english(p) for p in parts]


def _find_phrase_segments(words: List[Dict[str, Any]], phrases: List[str]) -> List[Dict[str, Any]]:
    if not phrases or not words:
        return []

    normalized_words = []
    for w in words:
        text = w.get("word", "")
        norm = _normalize_arabic(text) if _contains_arabic(text) else _normalize_english(text)
        normalized_words.append(norm)

    phrase_tokens_list = [p for p in (_tokenize_phrase(p) for p in phrases) if p]
    matches = []
    used_indices = set()

    for phrase_tokens in phrase_tokens_list:
        n = len(phrase_tokens)
        if n == 0:
            continue
        for i in range(0, len(normalized_words) - n + 1):
            if any(idx in used_indices for idx in range(i, i + n)):
                continue
            if normalized_words[i:i + n] == phrase_tokens:
                start = words[i].get("start", 0.0)
                end = words[i + n - 1].get("end", start)
                text = " ".join([words[j].get("word", "") for j in range(i, i + n)])
                matches.append({
                    "start": start,
                    "end": end,
                    "text": text,
                    "type": "filler_phrase"
                })
                used_indices.update(range(i, i + n))

    return matches


def _merge_segments(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not segments:
        return []

    segments_sorted = sorted(segments, key=lambda s: s["start"])
    merged = []
    current = dict(segments_sorted[0])
    current.setdefault("types", set([current.get("type", "cut")]))
    current.setdefault("sources", [segments_sorted[0]])

    for seg in segments_sorted[1:]:
        seg_start = seg["start"]
        seg_end = seg["end"]
        if seg_start <= current["end"]:
            current["end"] = max(current["end"], seg_end)
            current["types"].update([seg.get("type", "cut")])
            current["sources"].append(seg)
        else:
            current["types"] = sorted(list(current["types"]))
            current["duration"] = max(0.0, current["end"] - current["start"])
            merged.append(current)
            current = dict(seg)
            current.setdefault("types", set([current.get("type", "cut")]))
            current.setdefault("sources", [seg])

    current["types"] = sorted(list(current["types"]))
    current["duration"] = max(0.0, current["end"] - current["start"])
    merged.append(current)
    return merged


def _build_keep_segments(duration: float, cut_segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if duration <= 0:
        return []
    if not cut_segments:
        return [{"start": 0.0, "end": duration, "duration": duration}]

    keep = []
    cursor = 0.0
    for seg in cut_segments:
        if seg["start"] > cursor:
            keep.append({
                "start": cursor,
                "end": seg["start"],
                "duration": seg["start"] - cursor
            })
        cursor = max(cursor, seg["end"])
    if cursor < duration:
        keep.append({
            "start": cursor,
            "end": duration,
            "duration": duration - cursor
        })
    return keep


@register_tool
class SilenceCutterTool(BaseTool):
    """Detects silence and filler words and optionally cuts video/audio."""

    tool_id = "silence_cutter"
    tool_name = "Silence Cutter Tool"
    description = "Detect silence and filler words, produce cut list and optionally trimmed media"
    category = "audio"
    version = "1.0.0"

    def __init__(self):
        super().__init__()
        self.logger.info("Silence Cutter tool initialized")

    def execute(self, operation: str, **kwargs) -> ToolResult:
        operations = {
            "cut_silence": self.cut_silence,
            "analyze": self.analyze_audio
        }
        if operation not in operations:
            return ToolResult.fail(f"Unknown operation: {operation}")
        result = operations[operation](**kwargs)
        if isinstance(result, dict):
            return ToolResult.ok(data=result) if result.get("success") else ToolResult.fail(result.get("error", "Unknown error"))
        return result

    def analyze_audio(
        self,
        audio_path: str,
        threshold_db: float = DEFAULT_SETTINGS["threshold_db"],
        min_silence_duration: float = DEFAULT_SETTINGS["min_silence_duration"],
        padding: float = DEFAULT_SETTINGS["padding"],
        chunk_ms: int = DEFAULT_SETTINGS["chunk_ms"]
    ) -> Dict[str, Any]:
        """Analyze audio and return silence segments (no cutting)."""
        analysis = self._detect_silence_segments(
            audio_path=audio_path,
            threshold_db=threshold_db,
            min_silence_duration=min_silence_duration,
            chunk_ms=chunk_ms
        )
        if not analysis.get("success"):
            return analysis

        silence_segments = analysis.get("silence_segments", [])
        duration = analysis.get("audio_duration", 0.0)

        # Apply padding to silence segments to avoid clipping speech
        padded = self._apply_padding(silence_segments, padding, duration)
        merged = _merge_segments(padded)
        keep_segments = _build_keep_segments(duration, merged)

        return {
            "success": True,
            "audio_duration": duration,
            "silence_segments": silence_segments,
            "cut_segments": merged,
            "keep_segments": keep_segments
        }

    def cut_silence(
        self,
        audio_path: str,
        video_path: Optional[str] = None,
        output_path: Optional[str] = None,
        output_audio_path: Optional[str] = None,
        threshold_db: float = DEFAULT_SETTINGS["threshold_db"],
        min_silence_duration: float = DEFAULT_SETTINGS["min_silence_duration"],
        padding: float = DEFAULT_SETTINGS["padding"],
        chunk_ms: int = DEFAULT_SETTINGS["chunk_ms"],
        filler_detection: bool = DEFAULT_SETTINGS["filler_detection"],
        filler_model_size: str = DEFAULT_SETTINGS["filler_model_size"],
        filler_language: str = DEFAULT_SETTINGS["filler_language"],
        filler_confidence: float = DEFAULT_SETTINGS["filler_confidence"],
        filler_aggressive: bool = DEFAULT_SETTINGS["filler_aggressive"],
        filler_engine: str = DEFAULT_SETTINGS["filler_engine"],
        filler_words: Optional[Dict[str, List[str]]] = None,
        filler_phrases: Optional[Dict[str, List[str]]] = None,
        manual_cut_segments: Optional[List[Any]] = None,
        cut_list_path: Optional[str] = None,
        codec: str = "libx264",
        preset: str = "medium",
        crf: int = 23
    ) -> Dict[str, Any]:
        """
        Detect silence/filler segments and optionally cut video/audio.
        Returns cut list and paths to trimmed outputs if created.
        If manual_cut_segments is provided, it overrides automatic detection.
        """
        # Validate audio
        audio_error = self.validate_file_exists(audio_path)
        if audio_error:
            return {"success": False, "error": audio_error}

        settings = {
            "threshold_db": threshold_db,
            "min_silence_duration": min_silence_duration,
            "padding": padding,
            "chunk_ms": chunk_ms,
            "filler_detection": filler_detection,
            "filler_model_size": filler_model_size,
            "filler_language": filler_language,
            "filler_confidence": filler_confidence,
            "filler_aggressive": filler_aggressive,
            "filler_engine": filler_engine
        }
        manual_mode = manual_cut_segments is not None
        cut_mode = "auto"

        if manual_mode:
            duration, duration_error = self._get_audio_duration(audio_path)
            if duration_error:
                return {"success": False, "error": duration_error}

            manual_segments = self._normalize_manual_cut_segments(manual_cut_segments, duration)
            if not manual_segments:
                return {"success": False, "error": "No valid manual cut segments provided"}

            cut_mode = "manual"
            silence_segments = []
            filler_segments = []
            cut_segments = _merge_segments(manual_segments)
            keep_segments = _build_keep_segments(duration, cut_segments)
            settings["manual_cut_segments"] = manual_segments
        else:
            # Analyze silence
            analysis = self._detect_silence_segments(
                audio_path=audio_path,
                threshold_db=threshold_db,
                min_silence_duration=min_silence_duration,
                chunk_ms=chunk_ms
            )
            if not analysis.get("success"):
                return analysis

            silence_segments = analysis.get("silence_segments", [])
            duration = analysis.get("audio_duration", 0.0)

            filler_segments: List[Dict[str, Any]] = []
            if filler_detection:
                filler_segments = self._detect_filler_segments(
                    audio_path=audio_path,
                    model_size=filler_model_size,
                    language=filler_language,
                    min_confidence=filler_confidence,
                    aggressive=filler_aggressive,
                    filler_words=filler_words,
                    filler_phrases=filler_phrases,
                    filler_engine=filler_engine
                )

            # Combine segments
            segments = []
            segments.extend([dict(s, type="silence") for s in silence_segments])
            segments.extend([dict(s, type="filler") for s in filler_segments])

            # Apply padding (micro margins)
            padded = self._apply_padding(segments, padding, duration)
            cut_segments = _merge_segments(padded)
            keep_segments = _build_keep_segments(duration, cut_segments)

        settings["cut_mode"] = cut_mode

        # Save cut list
        cut_list = {
            "audio_path": audio_path,
            "video_path": video_path,
            "settings": settings,
            "cut_mode": cut_mode,
            "audio_duration": duration,
            "silence_segments": silence_segments,
            "filler_segments": filler_segments,
            "cut_segments": cut_segments,
            "keep_segments": keep_segments
        }
        if manual_mode:
            cut_list["manual_cut_segments"] = settings.get("manual_cut_segments", [])

        cut_list_path = self._resolve_cut_list_path(cut_list_path, output_path, output_audio_path, audio_path)
        if cut_list_path:
            Path(cut_list_path).parent.mkdir(parents=True, exist_ok=True)
            with open(cut_list_path, "w", encoding="utf-8") as f:
                json.dump(cut_list, f, indent=2, ensure_ascii=False)

        output_video_path = None
        output_audio_path_resolved = None

        # Optionally cut video/audio
        if video_path and output_path:
            video_error = self.validate_file_exists(video_path)
            if video_error:
                return {"success": False, "error": video_error}

            self.ensure_output_dir(output_path)
            cut_ok, cut_err = self._render_cut_video(
                video_path=video_path,
                keep_segments=keep_segments,
                output_path=output_path,
                codec=codec,
                preset=preset,
                crf=crf
            )
            if not cut_ok:
                return {"success": False, "error": cut_err or "Failed to cut video"}
            output_video_path = output_path

        if output_audio_path:
            self.ensure_output_dir(output_audio_path)
            audio_ok, audio_err = self._render_cut_audio(
                audio_path=audio_path,
                keep_segments=keep_segments,
                output_path=output_audio_path
            )
            if not audio_ok:
                return {"success": False, "error": audio_err or "Failed to cut audio"}
            output_audio_path_resolved = output_audio_path

        return {
            "success": True,
            "audio_duration": duration,
            "cut_mode": cut_mode,
            "silence_segments": silence_segments,
            "filler_segments": filler_segments,
            "cut_segments": cut_segments,
            "keep_segments": keep_segments,
            "manual_cut_segments": settings.get("manual_cut_segments", []) if manual_mode else [],
            "cut_list_path": cut_list_path,
            "output_video_path": output_video_path,
            "output_audio_path": output_audio_path_resolved
        }

    # -----------------------------
    # Internal helpers
    # -----------------------------
    def _get_audio_duration(self, audio_path: str) -> Tuple[Optional[float], Optional[str]]:
        try:
            with wave.open(audio_path, "rb") as wf:
                sample_rate = wf.getframerate()
                total_frames = wf.getnframes()
                duration = total_frames / float(sample_rate) if sample_rate else 0.0
                return duration, None
        except wave.Error as e:
            return None, f"Invalid WAV file: {e}"
        except Exception as e:
            return None, str(e)

    def _normalize_manual_cut_segments(
        self,
        segments: Optional[List[Any]],
        duration: float
    ) -> List[Dict[str, Any]]:
        if not segments:
            return []

        normalized: List[Dict[str, Any]] = []
        for seg in segments:
            start = None
            end = None

            if isinstance(seg, dict):
                start = seg.get("start", seg.get("from"))
                end = seg.get("end", seg.get("to"))
            elif isinstance(seg, (list, tuple)) and len(seg) >= 2:
                start, end = seg[0], seg[1]

            if start is None or end is None:
                continue

            if isinstance(start, str):
                start = _parse_timecode_to_seconds(start)
            if isinstance(end, str):
                end = _parse_timecode_to_seconds(end)

            try:
                start_val = float(start)
                end_val = float(end)
            except Exception:
                continue

            if end_val < start_val:
                start_val, end_val = end_val, start_val

            if duration > 0:
                start_val = max(0.0, min(start_val, duration))
                end_val = max(0.0, min(end_val, duration))

            if end_val <= start_val:
                continue

            normalized.append({
                "start": start_val,
                "end": end_val,
                "duration": end_val - start_val,
                "type": "manual"
            })

        return normalized

    def _resolve_cut_list_path(
        self,
        cut_list_path: Optional[str],
        output_path: Optional[str],
        output_audio_path: Optional[str],
        audio_path: str
    ) -> Optional[str]:
        if cut_list_path:
            return cut_list_path
        base_dir = None
        if output_path:
            base_dir = Path(output_path).parent
        elif output_audio_path:
            base_dir = Path(output_audio_path).parent
        else:
            base_dir = Path(audio_path).parent
        return str(base_dir / "silence_cut_list.json")

    def _detect_silence_segments(
        self,
        audio_path: str,
        threshold_db: float,
        min_silence_duration: float,
        chunk_ms: int
    ) -> Dict[str, Any]:
        try:
            if threshold_db is not None and threshold_db > 0:
                threshold_db = -abs(threshold_db)
            with wave.open(audio_path, "rb") as wf:
                sample_rate = wf.getframerate()
                channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                total_frames = wf.getnframes()

                duration = total_frames / float(sample_rate) if sample_rate else 0.0
                chunk_size = max(1, int(sample_rate * (chunk_ms / 1000.0)))
                max_possible = float(2 ** (sampwidth * 8 - 1))

                silence_segments = []
                in_silence = False
                silence_start = 0.0
                time_cursor = 0.0

                while True:
                    frames = wf.readframes(chunk_size)
                    if not frames:
                        break

                    if channels > 1:
                        frames = audioop.tomono(frames, sampwidth, 1, 1)

                    rms = audioop.rms(frames, sampwidth)
                    if rms <= 0:
                        dbfs = -100.0
                    else:
                        dbfs = 20.0 * math.log10(rms / max_possible)

                    chunk_duration = len(frames) / float(sampwidth) / float(sample_rate)
                    chunk_start = time_cursor
                    chunk_end = time_cursor + chunk_duration

                    if dbfs < threshold_db:
                        if not in_silence:
                            in_silence = True
                            silence_start = chunk_start
                    else:
                        if in_silence:
                            silence_end = chunk_start
                            if (silence_end - silence_start) >= min_silence_duration:
                                silence_segments.append({
                                    "start": silence_start,
                                    "end": silence_end,
                                    "duration": silence_end - silence_start
                                })
                            in_silence = False

                    time_cursor = chunk_end

                if in_silence:
                    silence_end = duration
                    if (silence_end - silence_start) >= min_silence_duration:
                        silence_segments.append({
                            "start": silence_start,
                            "end": silence_end,
                            "duration": silence_end - silence_start
                        })

                return {
                    "success": True,
                    "audio_duration": duration,
                    "silence_segments": silence_segments
                }

        except wave.Error as e:
            return {"success": False, "error": f"Invalid WAV file: {e}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _apply_padding(
        self,
        segments: List[Dict[str, Any]],
        padding: float,
        duration: float
    ) -> List[Dict[str, Any]]:
        padded = []
        for seg in segments:
            start = max(0.0, float(seg.get("start", 0.0)) + padding)
            end = min(duration, float(seg.get("end", 0.0)) - padding)
            if end <= start:
                continue
            new_seg = dict(seg)
            new_seg["start"] = start
            new_seg["end"] = end
            new_seg["duration"] = end - start
            padded.append(new_seg)
        return padded

    def _detect_filler_segments(
        self,
        audio_path: str,
        model_size: str,
        language: str,
        min_confidence: float,
        aggressive: bool,
        filler_words: Optional[Dict[str, List[str]]],
        filler_phrases: Optional[Dict[str, List[str]]],
        filler_engine: str
    ) -> List[Dict[str, Any]]:
        engine = (filler_engine or "whisper").lower()

        # Merge custom filler words/phrases if provided
        custom_words = filler_words or {}
        custom_phrases = filler_phrases or {}

        # Prefer Vosk if requested and available with model path
        if engine in ("vosk", "auto"):
            segments = self._detect_filler_segments_vosk(audio_path, aggressive, custom_words, custom_phrases)
            if segments is not None:
                return segments

        return self._detect_filler_segments_whisper(
            audio_path=audio_path,
            model_size=model_size,
            language=language,
            min_confidence=min_confidence,
            aggressive=aggressive,
            custom_words=custom_words,
            custom_phrases=custom_phrases
        )

    def _detect_filler_segments_vosk(
        self,
        audio_path: str,
        aggressive: bool,
        custom_words: Dict[str, List[str]],
        custom_phrases: Dict[str, List[str]]
    ) -> Optional[List[Dict[str, Any]]]:
        try:
            import vosk  # type: ignore
        except Exception:
            return None

        # Require model path in env or alongside audio
        model_path = Path("D:/Video Editing Project/editbot/.models/vosk")
        if not model_path.exists():
            return None

        try:
            model = vosk.Model(str(model_path))
            with wave.open(audio_path, "rb") as wf:
                if wf.getnchannels() != 1 or wf.getframerate() != 16000:
                    return None
                rec = vosk.KaldiRecognizer(model, wf.getframerate())
                rec.SetWords(True)
                results = []
                while True:
                    data = wf.readframes(4000)
                    if not data:
                        break
                    if rec.AcceptWaveform(data):
                        res = json.loads(rec.Result())
                        results.append(res)
                results.append(json.loads(rec.FinalResult()))

            words = []
            for res in results:
                for w in res.get("result", []):
                    words.append({
                        "word": w.get("word", ""),
                        "start": float(w.get("start", 0.0)),
                        "end": float(w.get("end", 0.0)),
                        "confidence": float(w.get("conf", 1.0))
                    })

            return self._filter_filler_words(
                words=words,
                aggressive=aggressive,
                custom_words=custom_words,
                custom_phrases=custom_phrases,
                min_confidence=0.0
            )
        except Exception:
            return None

    def _detect_filler_segments_whisper(
        self,
        audio_path: str,
        model_size: str,
        language: str,
        min_confidence: float,
        aggressive: bool,
        custom_words: Dict[str, List[str]],
        custom_phrases: Dict[str, List[str]]
    ) -> List[Dict[str, Any]]:
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except Exception:
            return []

        device = "cpu"
        compute_type = "int8"

        try:
            import torch  # type: ignore
            if torch.cuda.is_available():
                device = "cuda"
                compute_type = "int8_float16"
        except Exception:
            pass

        model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
            download_root="D:/Video Editing Project/editbot/.models",
            num_workers=2
        )

        lang = None if (language == "auto" or not language) else language
        segments, _info = model.transcribe(
            audio_path,
            language=lang,
            task="transcribe",
            word_timestamps=True,
            beam_size=1,
            best_of=1,
            vad_filter=False
        )

        words = []
        for segment in segments:
            if not segment.words:
                continue
            for w in segment.words:
                words.append({
                    "word": w.word.strip(),
                    "start": w.start,
                    "end": w.end,
                    "confidence": getattr(w, "probability", 1.0)
                })

        return self._filter_filler_words(
            words=words,
            aggressive=aggressive,
            custom_words=custom_words,
            custom_phrases=custom_phrases,
            min_confidence=min_confidence
        )

    def _filter_filler_words(
        self,
        words: List[Dict[str, Any]],
        aggressive: bool,
        custom_words: Dict[str, List[str]],
        custom_phrases: Dict[str, List[str]],
        min_confidence: float
    ) -> List[Dict[str, Any]]:
        filler_segments: List[Dict[str, Any]] = []

        # Prepare phrase detection
        phrase_list = list(EN_FILLER_PHRASES) + list(AR_FILLER_PHRASES)
        phrase_list += custom_phrases.get("english", []) + custom_phrases.get("arabic", [])

        phrase_segments = _find_phrase_segments(words, phrase_list)
        phrase_indices = set()

        for seg in phrase_segments:
            filler_segments.append({
                "start": seg["start"],
                "end": seg["end"],
                "text": seg.get("text", ""),
                "type": "filler_phrase"
            })
            # Track indices roughly (best effort)
            for i, w in enumerate(words):
                if w.get("start") >= seg["start"] and w.get("end") <= seg["end"]:
                    phrase_indices.add(i)

        custom_en = set(custom_words.get("english", []))
        custom_ar = set(custom_words.get("arabic", []))
        custom_en_loose = set(custom_words.get("english_loose", []))
        custom_ar_loose = set(custom_words.get("arabic_loose", []))
        custom_translit = set(custom_words.get("arabic_translit", []))

        # Word-level fillers
        for idx, w in enumerate(words):
            if idx in phrase_indices:
                continue
            confidence = float(w.get("confidence", 1.0))
            if confidence < min_confidence:
                continue
            token = w.get("word", "")
            if _is_filler_token(token, aggressive=aggressive):
                filler_segments.append({
                    "start": float(w.get("start", 0.0)),
                    "end": float(w.get("end", 0.0)),
                    "text": token,
                    "type": "filler_word"
                })
                continue

            # Custom filler word lists
            token_norm_en = _normalize_english(token)
            token_norm_ar = _normalize_arabic(token)
            if token_norm_en and (token_norm_en in custom_en or token_norm_en in custom_translit):
                filler_segments.append({
                    "start": float(w.get("start", 0.0)),
                    "end": float(w.get("end", 0.0)),
                    "text": token,
                    "type": "filler_word"
                })
            elif token_norm_ar and token_norm_ar in custom_ar:
                filler_segments.append({
                    "start": float(w.get("start", 0.0)),
                    "end": float(w.get("end", 0.0)),
                    "text": token,
                    "type": "filler_word"
                })
            elif aggressive and token_norm_en and token_norm_en in custom_en_loose:
                filler_segments.append({
                    "start": float(w.get("start", 0.0)),
                    "end": float(w.get("end", 0.0)),
                    "text": token,
                    "type": "filler_word"
                })
            elif aggressive and token_norm_ar and token_norm_ar in custom_ar_loose:
                filler_segments.append({
                    "start": float(w.get("start", 0.0)),
                    "end": float(w.get("end", 0.0)),
                    "text": token,
                    "type": "filler_word"
                })

        return filler_segments

    def _render_cut_video(
        self,
        video_path: str,
        keep_segments: List[Dict[str, Any]],
        output_path: str,
        codec: str,
        preset: str,
        crf: int
    ) -> Tuple[bool, Optional[str]]:
        if not keep_segments:
            return False, "No keep segments to render"

        filter_parts = []
        concat_inputs = []
        for i, seg in enumerate(keep_segments):
            start = max(0.0, seg["start"])
            end = max(start, seg["end"])
            filter_parts.append(
                f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{i}]"
            )
            filter_parts.append(
                f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{i}]"
            )
            concat_inputs.append(f"[v{i}][a{i}]")

        filter_complex = ";".join(filter_parts) + ";" + "".join(concat_inputs)
        filter_complex += f"concat=n={len(keep_segments)}:v=1:a=1[outv][outa]"

        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", codec, "-preset", preset, "-crf", str(crf),
            "-c:a", "aac",
            "-movflags", "+faststart",
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return False, result.stderr or "ffmpeg failed"
        return True, None

    def _render_cut_audio(
        self,
        audio_path: str,
        keep_segments: List[Dict[str, Any]],
        output_path: str
    ) -> Tuple[bool, Optional[str]]:
        if not keep_segments:
            return False, "No keep segments to render"

        filter_parts = []
        concat_inputs = []
        for i, seg in enumerate(keep_segments):
            start = max(0.0, seg["start"])
            end = max(start, seg["end"])
            filter_parts.append(
                f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{i}]"
            )
            concat_inputs.append(f"[a{i}]")

        filter_complex = ";".join(filter_parts) + ";" + "".join(concat_inputs)
        filter_complex += f"concat=n={len(keep_segments)}:v=0:a=1[outa]"

        cmd = [
            "ffmpeg", "-y", "-i", audio_path,
            "-filter_complex", filter_complex,
            "-map", "[outa]",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return False, result.stderr or "ffmpeg failed"
        return True, None
