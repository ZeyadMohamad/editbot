"""
Stock footage tool for overlaying or inserting additional video/image assets.
Uses ffmpeg to composite or splice clips into a base video.
"""
from __future__ import annotations

import subprocess
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ffmpeg

from tools.base_tool import BaseTool, ToolResult, register_tool
from tools.ffmpeg_tool import SUPPORTED_VIDEO_EXTENSIONS


IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"]

STOCK_KEYWORDS = ["stock", "b-roll", "broll", "cutaway", "overlay", "insert", "footage", "clip"]

TIME_TOKEN_PATTERN = r"(?:\d+(?::\d+){1,2}(?:\.\d+)?|\d+(?:\.\d+)?)(?:\s*(?:ms|s|sec|secs|seconds))?"
TIME_RANGE_REGEX = re.compile(
    rf"(?:from|between)\s*({TIME_TOKEN_PATTERN})\s*(?:to|and|through|-)\s*({TIME_TOKEN_PATTERN})",
    re.IGNORECASE
)
TIME_RANGE_FALLBACK_REGEX = re.compile(
    rf"({TIME_TOKEN_PATTERN})\s*(?:to|through|-)\s*({TIME_TOKEN_PATTERN})",
    re.IGNORECASE
)
START_TIME_REGEX = re.compile(
    rf"(?:start(?:ing)?(?:\s+at)?|at)\s*({TIME_TOKEN_PATTERN})",
    re.IGNORECASE
)
DURATION_REGEX = re.compile(
    rf"(?:for|duration)\s*({TIME_TOKEN_PATTERN})\s*(?:s|sec|secs|seconds)?",
    re.IGNORECASE
)
SOURCE_RANGE_REGEX = re.compile(
    rf"(?:stock|source)\s*(?:video|clip|footage|b-?roll)\s*(?:from|between)\s*({TIME_TOKEN_PATTERN})\s*(?:to|and|through|-)\s*({TIME_TOKEN_PATTERN})",
    re.IGNORECASE
)
SOURCE_RANGE_POST_REGEX = re.compile(
    rf"(?:from|between)\s*({TIME_TOKEN_PATTERN})\s*(?:to|and|through|-)\s*({TIME_TOKEN_PATTERN})\s*(?:of|in)\s*(?:the\s+)?(?:stock|source|clip|footage|b-?roll)",
    re.IGNORECASE
)
PATH_REGEX = re.compile(
    r"([A-Za-z]:\\[^\n]*?\.(?:mp4|mov|mkv|avi|webm|m4v|mpeg|mpg|3gp|ts|mts|png|jpg|jpeg|webp|bmp|gif))",
    re.IGNORECASE
)

SIZE_PRESETS = {
    "full": 1.0,
    "half": 0.5,
    "third": 1.0 / 3.0,
    "quarter": 0.25,
    "small": 0.3,
    "medium": 0.5,
    "large": 0.75
}

SIZE_REGEX = re.compile(
    r"(?:size|scale)\s*[:=]?\s*(full|half|third|quarter|small|medium|large|\d+(?:\.\d+)?%)",
    re.IGNORECASE
)


def _parse_timecode_to_seconds(value: Any) -> Optional[float]:
    """Parse timecodes like 18.005, 1:02.5, 00:00:18.005 into seconds."""
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


def _fmt_time(value: float) -> str:
    return f"{value:.3f}"


def _is_image_path(path: str) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def _is_video_path(path: str) -> bool:
    return Path(path).suffix.lower() in [e.lower() for e in SUPPORTED_VIDEO_EXTENSIONS]


def _probe_media(path: str) -> Dict[str, Any]:
    probe = ffmpeg.probe(path)
    video_stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), None)

    duration = None
    if probe.get("format", {}).get("duration"):
        try:
            duration = float(probe["format"]["duration"])
        except Exception:
            duration = None
    if duration is None and video_stream and video_stream.get("duration"):
        try:
            duration = float(video_stream["duration"])
        except Exception:
            duration = None

    width = int(video_stream.get("width", 0)) if video_stream else 0
    height = int(video_stream.get("height", 0)) if video_stream else 0

    sample_rate = None
    channel_layout = None
    if audio_stream:
        try:
            sample_rate = int(audio_stream.get("sample_rate", 0)) or None
        except Exception:
            sample_rate = None
        channel_layout = audio_stream.get("channel_layout")

    return {
        "duration": duration or 0.0,
        "width": width,
        "height": height,
        "has_audio": audio_stream is not None,
        "audio_sample_rate": sample_rate,
        "audio_channel_layout": channel_layout or "stereo"
    }


def _normalize_mode(value: Any) -> str:
    mode = (value or "overlay").strip().lower()
    if mode in ("overlay", "above", "layer", "over"):
        return "overlay"
    if mode in ("insert", "splice", "cutaway", "extension", "replace"):
        return "insert"
    return mode


def _parse_percent(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text.endswith("%"):
            try:
                return float(text[:-1].strip()) / 100.0
            except Exception:
                return None
    return None


def _parse_size_ratio(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ratio = float(value)
        return ratio if 0 < ratio <= 1.0 else None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in SIZE_PRESETS:
            return SIZE_PRESETS[text]
        percent = _parse_percent(text)
        if percent is not None:
            return percent if 0 < percent <= 1.0 else None
        try:
            ratio = float(text)
            return ratio if 0 < ratio <= 1.0 else None
        except Exception:
            return None
    return None


def _parse_dimension(value: Any, base: int) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(round(float(value)))
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("auto", "-1"):
            return -1
        if text.endswith("px"):
            text = text[:-2].strip()
        percent = _parse_percent(text)
        if percent is not None and base:
            return int(round(base * percent))
        try:
            return int(round(float(text)))
        except Exception:
            return None
    return None


def _parse_offset(value: Any, base: int) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().lower()
        percent = _parse_percent(text)
        if percent is not None and base:
            return float(base * percent)
        if text.endswith("px"):
            text = text[:-2].strip()
        try:
            return float(text)
        except Exception:
            return None
    return None




def _clean_path(path: str) -> str:
    cleaned = (path or "").strip().strip('"').strip("'")
    cleaned = cleaned.rstrip(".,;:)")
    return cleaned


def _has_time_hint(text: str) -> bool:
    lower = text.lower()
    return any(tok in lower for tok in [":", "s", "sec", "seconds", "."])


def _find_time_range(text: str) -> Tuple[Optional[float], Optional[float], Optional[Tuple[int, int]]]:
    if not text:
        return None, None, None

    match = TIME_RANGE_REGEX.search(text)
    if match:
        start = _parse_timecode_to_seconds(match.group(1))
        end = _parse_timecode_to_seconds(match.group(2))
        if start is not None and end is not None:
            if end < start:
                start, end = end, start
            return start, end, match.span()

    match = TIME_RANGE_FALLBACK_REGEX.search(text)
    if match and _has_time_hint(match.group(0)):
        start = _parse_timecode_to_seconds(match.group(1))
        end = _parse_timecode_to_seconds(match.group(2))
        if start is not None and end is not None:
            if end < start:
                start, end = end, start
            return start, end, match.span()

    start_match = START_TIME_REGEX.search(text)
    if start_match:
        start = _parse_timecode_to_seconds(start_match.group(1))
        dur_match = DURATION_REGEX.search(text)
        if start is not None and dur_match:
            duration = _parse_timecode_to_seconds(dur_match.group(1))
            if duration and duration > 0:
                return start, start + duration, start_match.span()

    return None, None, None


def _scrub_source_range(text: str) -> str:
    if not text:
        return text
    match = SOURCE_RANGE_REGEX.search(text) or SOURCE_RANGE_POST_REGEX.search(text)
    if not match:
        return text
    start_idx, end_idx = match.span()
    return text[:start_idx] + " " + text[end_idx:]


def _split_prompt_clauses(text: str) -> List[str]:
    if not text:
        return []
    separators = (
        r"(?:\.\s+|;\s+|\n+"
        r"|,?\s+and\s+then\s+"
        r"|,?\s+then\s+"
        r"|,?\s+next\s+"
        r"|,?\s+after\s+that\s+"
        r"|,?\s+afterwards\s+"
        r"|,?\s+also\s+"
        r"|,?\s+and\s+(?:add|insert|overlay|splice|cut|trim|remove|delete|snip|excise)\b)"
    )
    parts = re.split(separators, text, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p and p.strip()]


def _infer_position_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    lower = text.lower()
    if "top left" in lower or "upper left" in lower:
        return "top_left"
    if "top right" in lower or "upper right" in lower:
        return "top_right"
    if "top center" in lower or "top middle" in lower:
        return "top_center"
    if "bottom left" in lower or "lower left" in lower:
        return "bottom_left"
    if "bottom right" in lower or "lower right" in lower:
        return "bottom_right"
    if "bottom center" in lower or "bottom middle" in lower:
        return "bottom_center"
    if "center" in lower or "middle" in lower:
        return "center"
    if "left" in lower:
        return "left"
    if "right" in lower:
        return "right"
    if "top" in lower:
        return "top_center"
    if "bottom" in lower:
        return "bottom_center"
    return None


def _infer_size_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    lower = text.lower()
    if "full frame" in lower or "full screen" in lower or "fullscreen" in lower:
        return "full"
    if "half" in lower:
        return "half"
    if "quarter" in lower:
        return "quarter"
    if "third" in lower:
        return "third"
    return None


def parse_stock_items_from_prompt(prompt: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    if not prompt:
        return [], []

    errors: List[str] = []
    prompt_text = prompt.strip()
    prompt_lower = prompt_text.lower()

    paths = [
        _clean_path(p)
        for p in PATH_REGEX.findall(prompt_text)
    ]
    paths = [p for p in paths if p]

    has_intent = bool(paths) or any(k in prompt_lower for k in STOCK_KEYWORDS)
    if not has_intent:
        return [], []

    if not paths:
        errors.append("No stock file path found in prompt.")
        return [], errors

    # Extract source range (inside stock clip) first to avoid confusing with main range
    source_start = source_end = None
    source_match = SOURCE_RANGE_REGEX.search(prompt_text) or SOURCE_RANGE_POST_REGEX.search(prompt_text)
    scrubbed_prompt = prompt_text
    if source_match:
        source_start = _parse_timecode_to_seconds(source_match.group(1))
        source_end = _parse_timecode_to_seconds(source_match.group(2))
        if source_start is not None and source_end is not None and source_end < source_start:
            source_start, source_end = source_end, source_start
        start_idx, end_idx = source_match.span()
        scrubbed_prompt = prompt_text[:start_idx] + " " + prompt_text[end_idx:]

    clauses = _split_prompt_clauses(prompt_text)
    global_start, global_end, _span = _find_time_range(scrubbed_prompt)

    mode = "overlay"
    if any(k in prompt_lower for k in ["insert", "cutaway", "replace", "splice"]):
        mode = "insert"
    elif any(k in prompt_lower for k in ["overlay", "above", "layer", "on top"]):
        mode = "overlay"

    position = _infer_position_from_text(prompt_text)
    size = _infer_size_from_text(prompt_text)
    size_match = SIZE_REGEX.search(prompt_text)
    if size_match:
        size = size_match.group(1).strip().lower()

    items: List[Dict[str, Any]] = []
    missing_time_range = False
    for path in paths:
        start_time = None
        end_time = None
        clause = None
        path_lower = path.lower()
        for candidate in clauses:
            if path_lower in candidate.lower():
                clause = candidate
                break
        if clause is None:
            for candidate in clauses:
                candidate_lower = candidate.lower()
                if any(k in candidate_lower for k in STOCK_KEYWORDS):
                    clause = candidate
                    break
        if clause:
            clause_scrubbed = _scrub_source_range(clause)
            start_time, end_time, _ = _find_time_range(clause_scrubbed)
        if start_time is None:
            start_time, end_time = global_start, global_end
        if start_time is None:
            missing_time_range = True

        item: Dict[str, Any] = {
            "path": path,
            "mode": mode
        }
        if start_time is not None:
            item["start_time"] = start_time
        if end_time is not None:
            item["end_time"] = end_time
        if source_start is not None:
            item["source_start"] = source_start
        if source_end is not None:
            item["source_end"] = source_end
        if position:
            item["position"] = position
        if size:
            item["size"] = size
        items.append(item)

    if missing_time_range:
        errors.append("No stock placement time range found (e.g., 'from 26s to 29.5s').")

    if errors:
        return [], errors

    return items, []
def _normalize_position(value: Any) -> Optional[Tuple[str, str]]:
    if not value:
        return None
    text = str(value).strip().lower()
    text = text.replace("-", "_").replace(" ", "_")

    if text in ("center", "middle"):
        return ("center", "center")
    if text in ("top", "upper"):
        return ("center", "top")
    if text in ("bottom", "lower"):
        return ("center", "bottom")
    if text == "left":
        return ("left", "center")
    if text == "right":
        return ("right", "center")

    h = "center"
    v = "center"
    if "left" in text:
        h = "left"
    elif "right" in text:
        h = "right"
    if "top" in text or "upper" in text:
        v = "top"
    elif "bottom" in text or "lower" in text:
        v = "bottom"

    return (h, v)


@register_tool
class StockFootageTool(BaseTool):
    """Overlay or insert stock footage into a base video."""

    tool_id = "stock_footage"
    tool_name = "Stock Footage Tool"
    description = "Overlay or insert additional video/image assets into a base video"
    category = "video"
    version = "1.0.0"

    def execute(self, operation: str, **kwargs) -> ToolResult:
        operations = {
            "apply": self.apply_stock_footage
        }
        if operation not in operations:
            return ToolResult.fail(f"Unknown operation: {operation}")
        result = operations[operation](**kwargs)
        if isinstance(result, dict):
            return ToolResult.ok(data=result) if result.get("success") else ToolResult.fail(result.get("error", "Unknown error"))
        return result

    def apply_stock_footage(
        self,
        video_path: str,
        stock_items: List[Dict[str, Any]],
        output_path: str,
        codec: str = "libx264",
        preset: str = "medium",
        crf: int = 23
    ) -> Dict[str, Any]:
        # Validate base video
        error = self.validate_file_exists(video_path)
        if error:
            return {"success": False, "error": error}
        if not _is_video_path(video_path):
            return {"success": False, "error": f"Unsupported base video format: {Path(video_path).suffix}"}

        if not stock_items:
            return {"success": False, "error": "No stock items provided"}

        self.ensure_output_dir(output_path)

        current_input = video_path
        output_path = str(output_path)
        final_output = output_path

        for idx, item in enumerate(stock_items):
            if not isinstance(item, dict):
                return {"success": False, "error": f"Invalid stock item at index {idx}"}

            stock_path = item.get("path") or item.get("file") or item.get("source")
            if not stock_path:
                return {"success": False, "error": f"Missing stock path for item {idx}"}
            stock_path = str(stock_path)
            if self.validate_file_exists(stock_path):
                return {"success": False, "error": f"Stock file not found: {stock_path}"}

            mode = _normalize_mode(item.get("mode"))
            if mode not in ("overlay", "insert"):
                return {"success": False, "error": f"Invalid mode '{mode}' for item {idx}. Use overlay or insert."}

            if idx < len(stock_items) - 1:
                temp_out = Path(output_path).with_name(
                    f"{Path(output_path).stem}_stock_{idx + 1}{Path(output_path).suffix}"
                )
                step_output = str(temp_out)
            else:
                step_output = final_output

            if mode == "overlay":
                ok, err = self._apply_overlay(
                    base_path=current_input,
                    stock_path=stock_path,
                    item=item,
                    output_path=step_output,
                    codec=codec,
                    preset=preset,
                    crf=crf
                )
            else:
                ok, err = self._apply_insert(
                    base_path=current_input,
                    stock_path=stock_path,
                    item=item,
                    output_path=step_output,
                    codec=codec,
                    preset=preset,
                    crf=crf
                )

            if not ok:
                return {"success": False, "error": err or "Stock footage processing failed"}

            current_input = step_output

        return {
            "success": True,
            "output_path": final_output,
            "video_file": final_output
        }

    # -----------------------------
    # Internal helpers
    # -----------------------------
    def _apply_overlay(
        self,
        base_path: str,
        stock_path: str,
        item: Dict[str, Any],
        output_path: str,
        codec: str,
        preset: str,
        crf: int
    ) -> Tuple[bool, Optional[str]]:
        base_info = _probe_media(base_path)
        start_time = _parse_timecode_to_seconds(item.get("start_time") or item.get("start"))
        end_time = _parse_timecode_to_seconds(item.get("end_time") or item.get("end"))
        duration = _parse_timecode_to_seconds(item.get("duration"))
        source_start = _parse_timecode_to_seconds(item.get("source_start"))
        source_end = _parse_timecode_to_seconds(item.get("source_end"))

        if start_time is None:
            return False, "Overlay requires start_time"

        overlay_duration = None
        if end_time is not None:
            overlay_duration = max(0.0, end_time - start_time)
        elif duration is not None:
            overlay_duration = max(0.0, duration)
        elif source_start is not None and source_end is not None:
            overlay_duration = max(0.0, source_end - source_start)

        stock_is_image = _is_image_path(stock_path)
        stock_is_video = _is_video_path(stock_path)
        if not stock_is_image and not stock_is_video:
            return False, f"Unsupported stock format: {Path(stock_path).suffix}"

        stock_info = _probe_media(stock_path)
        stock_duration = stock_info.get("duration", 0.0)
        stock_w = stock_info.get("width", 0)
        stock_h = stock_info.get("height", 0)

        if overlay_duration is None or overlay_duration <= 0:
            if stock_is_image:
                return False, "Overlay image requires duration or end_time"
            if stock_duration and source_start is not None:
                overlay_duration = max(0.0, stock_duration - source_start)
            else:
                overlay_duration = stock_duration or 0.0

        if overlay_duration <= 0:
            return False, "Could not determine overlay duration"

        base_w = base_info.get("width", 0)
        base_h = base_info.get("height", 0)

        width_raw = item.get("width") or item.get("w")
        height_raw = item.get("height") or item.get("h")
        size_raw = item.get("size") or item.get("scale")

        width = _parse_dimension(width_raw, base_w)
        height = _parse_dimension(height_raw, base_h)

        if size_raw is not None and width is None and height is None:
            ratio = _parse_size_ratio(size_raw)
            if ratio and base_w and base_h:
                width = int(round(base_w * ratio))
                height = int(round(base_h * ratio))

        if width is None and height is None:
            width = base_w
            height = base_h
        elif width is None:
            width = -1
        elif height is None:
            height = -1

        explicit_size = any(k in item for k in ("width", "w", "height", "h", "size", "scale"))

        x_raw = item.get("x")
        y_raw = item.get("y")
        x = _parse_offset(x_raw, base_w) if x_raw is not None else None
        y = _parse_offset(y_raw, base_h) if y_raw is not None else None

        if x is None or y is None:
            position = _normalize_position(item.get("position") or item.get("pos"))
            margin_raw = item.get("margin") or item.get("padding")
            margin_x = _parse_offset(margin_raw, base_w) or 0.0
            margin_y = _parse_offset(margin_raw, base_h) or 0.0

            overlay_w = width if isinstance(width, (int, float)) and width > 0 else base_w
            overlay_h = height if isinstance(height, (int, float)) and height > 0 else base_h

            if position:
                h_align, v_align = position
                if x is None:
                    if h_align == "left":
                        x = margin_x
                    elif h_align == "right":
                        x = max(0.0, base_w - overlay_w - margin_x)
                    else:
                        x = max(0.0, (base_w - overlay_w) / 2.0)
                if y is None:
                    if v_align == "top":
                        y = margin_y
                    elif v_align == "bottom":
                        y = max(0.0, base_h - overlay_h - margin_y)
                    else:
                        y = max(0.0, (base_h - overlay_h) / 2.0)

        if x is None:
            x = 0.0
        if y is None:
            y = 0.0

        stock_input_args: List[str] = []
        if stock_is_image:
            stock_input_args = ["-loop", "1", "-t", _fmt_time(overlay_duration), "-i", stock_path]
        else:
            if source_start is not None:
                stock_input_args += ["-ss", _fmt_time(source_start)]
            stock_input_args += ["-t", _fmt_time(overlay_duration), "-i", stock_path]

        filter_parts = []
        scale_part = ""
        scale_w = int(width) if width is not None else -1
        scale_h = int(height) if height is not None else -1
        if scale_w > 0 or scale_h > 0:
            scale_part = f"scale={scale_w}:{scale_h},"
        filter_parts.append(
            f"[1:v]{scale_part}setpts=PTS-STARTPTS+{_fmt_time(start_time)}/TB[ov]"
        )
        filter_parts.append(
            f"[0:v][ov]overlay=x={x}:y={y}:eof_action=pass[outv]"
        )
        filter_complex = ";".join(filter_parts)

        cmd = [
            "ffmpeg", "-y",
            "-i", base_path,
            *stock_input_args,
            "-filter_complex", filter_complex,
            "-map", "[outv]"
        ]

        if base_info.get("has_audio"):
            cmd += ["-map", "0:a?"]

        cmd += [
            "-c:v", codec,
            "-preset", preset,
            "-crf", str(crf)
        ]

        if base_info.get("has_audio"):
            cmd += ["-c:a", "aac"]

        cmd += ["-movflags", "+faststart", output_path]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return False, result.stderr or "ffmpeg overlay failed"
        return True, None

    def _apply_insert(
        self,
        base_path: str,
        stock_path: str,
        item: Dict[str, Any],
        output_path: str,
        codec: str,
        preset: str,
        crf: int
    ) -> Tuple[bool, Optional[str]]:
        base_info = _probe_media(base_path)
        base_duration = base_info.get("duration", 0.0)
        if base_duration <= 0:
            return False, "Base video duration is unknown"

        insert_at = _parse_timecode_to_seconds(item.get("start_time") or item.get("start"))
        if insert_at is None:
            return False, "Insert requires start_time"

        end_time = _parse_timecode_to_seconds(item.get("end_time") or item.get("end"))
        insert_at = max(0.0, min(insert_at, base_duration))

        source_start = _parse_timecode_to_seconds(item.get("source_start")) or 0.0
        source_end = _parse_timecode_to_seconds(item.get("source_end"))
        duration = _parse_timecode_to_seconds(item.get("duration"))
        target_duration = 0.0
        if end_time is not None:
            target_duration = max(0.0, end_time - insert_at)

        stock_is_image = _is_image_path(stock_path)
        stock_is_video = _is_video_path(stock_path)
        if not stock_is_image and not stock_is_video:
            return False, f"Unsupported stock format: {Path(stock_path).suffix}"

        stock_info = {"duration": 0.0, "has_audio": False}
        if stock_is_video:
            stock_info = _probe_media(stock_path)

        stock_duration = None
        if duration is not None:
            stock_duration = max(0.0, duration)
        elif source_end is not None:
            stock_duration = max(0.0, source_end - source_start)
        elif target_duration > 0:
            stock_duration = target_duration
        elif stock_is_video:
            stock_duration = max(0.0, stock_info.get("duration", 0.0) - source_start)

        if stock_is_image and (stock_duration is None or stock_duration <= 0):
            return False, "Insert image requires duration or end_time"

        if stock_duration is None or stock_duration <= 0:
            return False, "Could not determine stock duration"

        output_has_audio = bool(base_info.get("has_audio") or stock_info.get("has_audio"))

        sample_rate = base_info.get("audio_sample_rate") or stock_info.get("audio_sample_rate") or 44100
        channel_layout = base_info.get("audio_channel_layout") or stock_info.get("audio_channel_layout") or "stereo"

        inputs: List[str] = ["-i", base_path]

        if stock_is_image:
            inputs += ["-loop", "1", "-t", _fmt_time(stock_duration), "-i", stock_path]
        else:
            if source_start:
                inputs += ["-ss", _fmt_time(source_start)]
            inputs += ["-t", _fmt_time(stock_duration), "-i", stock_path]

        silence_input_index = None
        if output_has_audio and (not base_info.get("has_audio") or not stock_info.get("has_audio")):
            inputs += ["-f", "lavfi", "-i", f"anullsrc=channel_layout={channel_layout}:sample_rate={sample_rate}"]
            silence_input_index = 2

        filter_parts: List[str] = []
        segments: List[Dict[str, str]] = []

        pre_duration = insert_at
        post_duration = max(0.0, base_duration - insert_at)

        if pre_duration > 0:
            filter_parts.append(
                f"[0:v]trim=start=0:end={_fmt_time(insert_at)},setpts=PTS-STARTPTS[vpre]"
            )
            seg = {"v": "vpre"}
            if output_has_audio:
                if base_info.get("has_audio"):
                    filter_parts.append(
                        f"[0:a]atrim=start=0:end={_fmt_time(insert_at)},asetpts=PTS-STARTPTS[apre]"
                    )
                else:
                    filter_parts.append(
                        f"[{silence_input_index}:a]atrim=start=0:end={_fmt_time(insert_at)},asetpts=PTS-STARTPTS[apre]"
                    )
                seg["a"] = "apre"
            segments.append(seg)

        base_w = base_info.get("width", 0)
        base_h = base_info.get("height", 0)
        scale_part = ""
        if base_w and base_h:
            scale_part = f"scale={base_w}:{base_h},"
        filter_parts.append(
            f"[1:v]{scale_part}setpts=PTS-STARTPTS[vstock]"
        )
        seg = {"v": "vstock"}
        if output_has_audio:
            if stock_info.get("has_audio"):
                filter_parts.append(
                    f"[1:a]atrim=start=0:end={_fmt_time(stock_duration)},asetpts=PTS-STARTPTS[astock]"
                )
            else:
                filter_parts.append(
                    f"[{silence_input_index}:a]atrim=start=0:end={_fmt_time(stock_duration)},asetpts=PTS-STARTPTS[astock]"
                )
            seg["a"] = "astock"
        segments.append(seg)

        if post_duration > 0:
            filter_parts.append(
                f"[0:v]trim=start={_fmt_time(insert_at)}:end={_fmt_time(base_duration)},setpts=PTS-STARTPTS[vpost]"
            )
            seg = {"v": "vpost"}
            if output_has_audio:
                if base_info.get("has_audio"):
                    filter_parts.append(
                        f"[0:a]atrim=start={_fmt_time(insert_at)}:end={_fmt_time(base_duration)},asetpts=PTS-STARTPTS[apost]"
                    )
                else:
                    filter_parts.append(
                        f"[{silence_input_index}:a]atrim=start=0:end={_fmt_time(post_duration)},asetpts=PTS-STARTPTS[apost]"
                    )
                seg["a"] = "apost"
            segments.append(seg)

        if not segments:
            return False, "Insert produced no segments"

        if output_has_audio:
            concat_inputs = "".join([f"[{s['v']}][{s['a']}]" for s in segments])
            filter_parts.append(
                f"{concat_inputs}concat=n={len(segments)}:v=1:a=1[outv][outa]"
            )
        else:
            concat_inputs = "".join([f"[{s['v']}]" for s in segments])
            filter_parts.append(
                f"{concat_inputs}concat=n={len(segments)}:v=1:a=0[outv]"
            )

        filter_complex = ";".join(filter_parts)

        cmd = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[outv]"
        ]

        if output_has_audio:
            cmd += ["-map", "[outa]", "-c:a", "aac"]

        cmd += [
            "-c:v", codec,
            "-preset", preset,
            "-crf", str(crf),
            "-movflags", "+faststart",
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return False, result.stderr or "ffmpeg insert failed"
        return True, None
