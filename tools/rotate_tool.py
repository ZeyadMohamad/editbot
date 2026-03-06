"""
Rotate tool for rotating a single video or image by clockwise degrees.

Performance strategy (videos):
  1. Metadata rotation  – instant, lossless stream-copy for 90° multiples on
     MP4/MOV containers.  Just sets the display-matrix flag; no re-encode.
  2. Transpose / flip   – fast re-encode fallback for 90° multiples on other
     containers.  Integer pixel shuffle, no interpolation.
  3. Rotate filter      – full re-encode for arbitrary angles (e.g. 45°).

Images always use filters (transpose or rotate); single-frame is fast either way.
"""
from __future__ import annotations

import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from tools.base_tool import BaseTool, ToolResult, register_tool


SUPPORTED_VIDEO_EXTENSIONS = [
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv",
    ".webm", ".m4v", ".mpeg", ".mpg", ".3gp", ".ts", ".mts",
]
IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff"]

# Containers whose display-matrix supports lossless rotation via stream copy
_METADATA_CONTAINERS = {".mp4", ".mov", ".m4v", ".3gp"}

# Optimised filter expressions for exact 90° clockwise multiples
_TRANSPOSE_FILTERS: Dict[int, str] = {
    90:  "transpose=1",
    180: "hflip,vflip",
    270: "transpose=2",
}

ROTATION_COUNT_WORDS = {
    "once": 1.0,
    "twice": 2.0,
    "thrice": 3.0,
}


# ---------------------------------------------------------------------------
# Parsing helpers (unchanged logic)
# ---------------------------------------------------------------------------

def _normalize_cw_degrees(value: float) -> float:
    normalized = float(value) % 360.0
    if abs(normalized) < 1e-9:
        return 0.0
    return normalized


def _parse_rotation_count(value: str) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in ROTATION_COUNT_WORDS:
        return ROTATION_COUNT_WORDS[text]
    try:
        return float(text)
    except Exception:
        return None


def parse_rotation_cw_degrees(value: Any) -> Optional[float]:
    """
    Parse clockwise rotation degrees.

    Accepted examples:
    - 45
    - "45deg", "45 degrees"
    - "rotate right 2 times" (=> 180)
    - "rotate left 2 times" (=> 180 clockwise)
    - "rotate left once" (=> 270 clockwise)
    - "rotate 30 degrees counterclockwise" (=> 330 clockwise)
    """
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return _normalize_cw_degrees(float(value))

    text = str(value).strip().lower()
    if not text:
        return None

    degree_match = re.search(
        r"(?:(left|right|clockwise|counterclockwise|anticlockwise)\s*)?"
        r"(-?\d+(?:\.\d+)?)\s*(?:°|deg(?:ree)?s?)"
        r"(?:\s*(left|right|clockwise|counterclockwise|anticlockwise))?",
        text
    )
    if degree_match:
        direction = (degree_match.group(1) or degree_match.group(3) or "").strip()
        amount = float(degree_match.group(2))
        if direction in ("left", "counterclockwise", "anticlockwise"):
            amount = -abs(amount)
        elif direction in ("right", "clockwise"):
            amount = abs(amount)
        return _normalize_cw_degrees(amount)

    times_match = re.search(
        r"(?:\brotate\b(?:\s+(?:it|media|video|image))?\s*)?"
        r"\b(left|right)\b"
        r"(?:\s+(\d+(?:\.\d+)?|once|twice|thrice))?"
        r"(?:\s*(?:times|time|x|turn|turns))?",
        text
    )
    if times_match and "rotate" in text:
        direction = times_match.group(1)
        count_raw = times_match.group(2) or "1"
        count = _parse_rotation_count(count_raw)
        if count is None:
            count = 1.0
        amount = abs(count) * 90.0
        if direction == "left":
            amount = -amount
        return _normalize_cw_degrees(amount)

    reverse_times_match = re.search(
        r"(?:\brotate\b(?:\s+(?:it|media|video|image))?\s*)?"
        r"(\d+(?:\.\d+)?|once|twice|thrice)\s*(?:times|time|x|turn|turns)"
        r"\s*(?:to\s+the\s+)?\b(left|right)\b",
        text
    )
    if reverse_times_match:
        count = _parse_rotation_count(reverse_times_match.group(1))
        direction = reverse_times_match.group(2)
        if count is None:
            count = 1.0
        amount = abs(count) * 90.0
        if direction == "left":
            amount = -amount
        return _normalize_cw_degrees(amount)

    if "rotate" in text:
        if any(token in text for token in ("counterclockwise", "anticlockwise", "left")):
            return 270.0
        if any(token in text for token in ("clockwise", "right")):
            return 90.0

    raw_numeric = re.fullmatch(r"-?\d+(?:\.\d+)?", text)
    if raw_numeric:
        return _normalize_cw_degrees(float(text))

    return None


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

@register_tool
class RotateTool(BaseTool):
    """Rotate single video/image media and save output."""

    tool_id = "rotate_media"
    tool_name = "Rotate Tool"
    description = "Rotate a single video or image by clockwise degrees"
    category = "video"
    version = "2.0.0"

    def __init__(self, ffmpeg_path: Optional[str] = None):
        super().__init__()
        self.ffmpeg_path = ffmpeg_path or "ffmpeg"

    # -- public interface ---------------------------------------------------

    def execute(self, operation: str, **kwargs) -> ToolResult:
        operations = {
            "rotate_media": self.rotate_media,
            "rotate": self.rotate_media,
        }
        if operation not in operations:
            return ToolResult.fail(f"Unknown operation: {operation}")
        result = operations[operation](**kwargs)
        if isinstance(result, dict):
            return ToolResult.ok(data=result) if result.get("success") else ToolResult.fail(result.get("error", "Unknown error"))
        return result

    def rotate_media(
        self,
        input_path: str,
        output_path: str,
        rotation: Any = None,
        rotation_cw_deg: Any = None,
        codec: str = "libx264",
        preset: str = "medium",
        crf: int = 23,
        image_quality: int = 2,
    ) -> Dict[str, Any]:
        error, media_type = self._validate_media_path(input_path)
        if error:
            return {"success": False, "error": error}

        ext_out = Path(output_path).suffix.lower()
        if media_type == "video":
            output_ext_error = self.validate_file_extension(output_path, SUPPORTED_VIDEO_EXTENSIONS)
            if output_ext_error:
                return {"success": False, "error": output_ext_error}
        elif ext_out not in IMAGE_EXTENSIONS:
            return {"success": False, "error": f"Unsupported output image format: {ext_out}"}

        degrees_input = rotation_cw_deg if rotation_cw_deg is not None else rotation
        degrees = parse_rotation_cw_degrees(degrees_input)
        if degrees is None:
            return {"success": False, "error": "Could not parse rotation. Use degrees or rotate left/right with times."}

        self.ensure_output_dir(output_path)

        if degrees == 0.0:
            shutil.copy2(input_path, output_path)
            return self._ok(output_path, 0.0, media_type, "copy")

        if media_type == "image":
            return self._rotate_image(input_path, output_path, degrees, image_quality)

        # Video: pick the fastest available method
        is_90 = self._is_exact_90_multiple(degrees)
        if is_90:
            ext_in = Path(input_path).suffix.lower()
            if ext_in in _METADATA_CONTAINERS and ext_out in _METADATA_CONTAINERS:
                result = self._rotate_video_metadata(input_path, output_path, degrees)
                if result is not None:
                    return result
            return self._rotate_video_transpose(
                input_path, output_path, degrees, codec, preset, crf,
            )
        return self._rotate_video_arbitrary(
            input_path, output_path, degrees, codec, preset, crf,
        )

    # -- private: validation ------------------------------------------------

    def _validate_media_path(self, media_path: str) -> Tuple[Optional[str], Optional[str]]:
        error = self.validate_file_exists(media_path)
        if error:
            return error, None
        ext = Path(media_path).suffix.lower()
        if ext in SUPPORTED_VIDEO_EXTENSIONS:
            return None, "video"
        if ext in IMAGE_EXTENSIONS:
            return None, "image"
        return f"Unsupported media format: {ext}", None

    @staticmethod
    def _is_exact_90_multiple(degrees: float) -> bool:
        return degrees > 0 and abs(degrees % 90.0) < 1e-9 and degrees < 360

    # -- private: result helpers --------------------------------------------

    @staticmethod
    def _ok(output_path: str, degrees: float, media_type: str, method: str) -> Dict[str, Any]:
        return {
            "success": True,
            "output_path": output_path,
            "media_file": output_path,
            "rotation_cw_deg": degrees,
            "media_type": media_type,
            "method": method,
        }

    @staticmethod
    def _fail(error: str) -> Dict[str, Any]:
        return {"success": False, "error": error}

    # -- private: video strategies ------------------------------------------

    def _rotate_video_metadata(
        self, input_path: str, output_path: str, degrees: float,
    ) -> Optional[Dict[str, Any]]:
        """Instant lossless rotation via container display-matrix (stream copy)."""
        cmd = [
            self.ffmpeg_path, "-y",
            "-noautorotate",
            "-i", input_path,
            "-c", "copy",
            "-map_metadata", "0",
            "-metadata:s:v:0", f"rotate={int(degrees)}",
            "-movflags", "+faststart",
            output_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            return None  # signal caller to fall back
        return self._ok(output_path, degrees, "video", "metadata")

    def _rotate_video_transpose(
        self, input_path: str, output_path: str, degrees: float,
        codec: str, preset: str, crf: int,
    ) -> Dict[str, Any]:
        """Fast re-encode for exact 90° multiples using transpose/flip filters."""
        vf = _TRANSPOSE_FILTERS[int(degrees)]
        vf += ",scale=trunc(iw/2)*2:trunc(ih/2)*2"
        cmd = [
            self.ffmpeg_path, "-y",
            "-i", input_path,
            "-vf", vf,
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", codec, "-pix_fmt", "yuv420p",
            "-profile:v", "main", "-preset", preset, "-crf", str(crf),
            "-c:a", "aac", "-movflags", "+faststart",
            output_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            return self._fail(proc.stderr or "ffmpeg transpose failed")
        return self._ok(output_path, degrees, "video", "transpose")

    def _rotate_video_arbitrary(
        self, input_path: str, output_path: str, degrees: float,
        codec: str, preset: str, crf: int,
    ) -> Dict[str, Any]:
        """Arbitrary-angle rotation via the rotate filter (full re-encode)."""
        # Negate: ffmpeg rotate filter treats positive angles as counterclockwise
        angle_rad = -(degrees * math.pi / 180.0)
        vf = (
            f"rotate={angle_rad:.10f}:ow=rotw({angle_rad:.10f}):oh=roth({angle_rad:.10f}):c=black,"
            "scale=trunc(iw/2)*2:trunc(ih/2)*2"
        )
        cmd = [
            self.ffmpeg_path, "-y",
            "-i", input_path,
            "-vf", vf,
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", codec, "-pix_fmt", "yuv420p",
            "-profile:v", "main", "-preset", preset, "-crf", str(crf),
            "-c:a", "aac", "-movflags", "+faststart",
            output_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            return self._fail(proc.stderr or "ffmpeg rotate failed")
        return self._ok(output_path, degrees, "video", "rotate_filter")

    # -- private: image strategies ------------------------------------------

    def _rotate_image(
        self, input_path: str, output_path: str, degrees: float, image_quality: int,
    ) -> Dict[str, Any]:
        ext_out = Path(output_path).suffix.lower()
        if self._is_exact_90_multiple(degrees):
            vf = _TRANSPOSE_FILTERS[int(degrees)]
            method = "transpose"
        else:
            # Negate: ffmpeg rotate filter treats positive angles as counterclockwise
            angle_rad = -(degrees * math.pi / 180.0)
            fill = "none" if ext_out in (".png", ".webp", ".gif") else "black"
            vf = f"rotate={angle_rad:.10f}:ow=rotw({angle_rad:.10f}):oh=roth({angle_rad:.10f}):c={fill}"
            method = "rotate_filter"

        cmd = [self.ffmpeg_path, "-y", "-i", input_path, "-vf", vf, "-frames:v", "1"]
        if ext_out in (".jpg", ".jpeg"):
            cmd += ["-q:v", str(image_quality)]
        cmd.append(output_path)

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            return self._fail(proc.stderr or "ffmpeg image rotate failed")
        return self._ok(output_path, degrees, "image", method)
