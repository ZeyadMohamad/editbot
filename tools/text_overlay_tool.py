"""
Text overlay tool for adding arbitrary styled text to video.

Supports rich text with per-segment formatting, animations (fade, slide,
typewriter), background boxes, shadows, strokes, and precise positioning.
Generates ASS subtitle files and burns them via FFmpeg.
"""
from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from tools.base_tool import BaseTool, ToolResult, register_tool
from core.logging import setup_logger

logger = setup_logger("text_overlay_tool")

NAMED_POSITIONS = {
    "center":        (0.5, 0.5),
    "top_left":      (0.1, 0.1),
    "top_center":    (0.5, 0.1),
    "top_right":     (0.9, 0.1),
    "middle_left":   (0.1, 0.5),
    "middle_right":  (0.9, 0.5),
    "bottom_left":   (0.1, 0.9),
    "bottom_center": (0.5, 0.9),
    "bottom_right":  (0.9, 0.9),
}

# ASS alignment values mapped from position names
ASS_ALIGNMENT = {
    "top_left": 7, "top_center": 8, "top_right": 9,
    "middle_left": 4, "center": 5, "middle_right": 6,
    "bottom_left": 1, "bottom_center": 2, "bottom_right": 3,
}

DEFAULT_FONT = "Montserrat ExtraBold"
DEFAULT_FONT_SIZE = 48
DEFAULT_COLOR = "FFFFFF"


def _hex_to_ass(hex_color: str) -> str:
    """Convert RRGGBB hex to ASS &HBBGGRR& format."""
    h = hex_color.lstrip("#")
    if len(h) == 6:
        r, g, b = h[0:2], h[2:4], h[4:6]
        return f"&H00{b}{g}{r}&"
    return f"&H00{h}&"


def _hex_to_ass_alpha(hex_color: str, alpha: float = 1.0) -> str:
    """Convert RRGGBB hex + alpha to ASS &HAABBGGRR format."""
    h = hex_color.lstrip("#")
    a = format(int((1.0 - alpha) * 255), "02X")
    if len(h) == 6:
        r, g, b = h[0:2], h[2:4], h[4:6]
        return f"&H{a}{b}{g}{r}"
    return f"&H{a}{h}"


def _ass_timestamp(seconds: float) -> str:
    """Format seconds as ASS timestamp h:mm:ss.cs."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _get_video_dimensions(video_path: str) -> Tuple[int, int]:
    """Get video width and height using ffmpeg.probe."""
    try:
        import ffmpeg
        probe = ffmpeg.probe(video_path)
        video_stream = next(
            (s for s in probe["streams"] if s["codec_type"] == "video"), None
        )
        if video_stream:
            return int(video_stream["width"]), int(video_stream["height"])
    except Exception as e:
        logger.warning(f"Failed to probe video: {e}")
    return 1920, 1080  # fallback


def _resolve_position(
    position: Union[str, Tuple[int, int], List[int]],
    video_w: int,
    video_h: int,
) -> Tuple[int, int]:
    """Resolve named or tuple position to pixel coordinates."""
    if isinstance(position, str):
        frac = NAMED_POSITIONS.get(position, (0.5, 0.5))
        return int(frac[0] * video_w), int(frac[1] * video_h)
    return int(position[0]), int(position[1])


def _build_animation_tags(
    anim_type: str,
    duration_ms: int,
    direction: str = "in",
    video_w: int = 1920,
    video_h: int = 1080,
) -> str:
    """Build ASS override tags for entry/exit animations."""
    if anim_type == "none" or not anim_type:
        return ""

    if anim_type == "fade":
        if direction == "in":
            return f"\\fad({duration_ms},0)"
        return f"\\fad(0,{duration_ms})"

    move_dist = 200
    if anim_type == "slide_up":
        offset = move_dist if direction == "in" else -move_dist
        return f"\\move({{x}},{{y_offset_{direction}_{offset}}},{{x}},{{y}},0,{duration_ms})" if direction == "in" else ""
    if anim_type == "slide_down":
        offset = -move_dist if direction == "in" else move_dist
        return f"\\move({{x}},{{y_offset_{direction}_{offset}}},{{x}},{{y}},0,{duration_ms})" if direction == "in" else ""
    if anim_type == "scale":
        if direction == "in":
            return f"\\t(0,{duration_ms},\\fscx100\\fscy100)\\fscx10\\fscy10"
        return f"\\t(0,{duration_ms},\\fscx10\\fscy10)"

    return ""


@register_tool
class TextOverlayTool(BaseTool):
    """Add arbitrary styled text overlays to video."""

    tool_id = "text_overlay"
    tool_name = "Text Overlay"
    description = "Add styled text overlays with animations to video"
    category = "video"
    version = "1.0.0"

    def execute(self, **kwargs) -> ToolResult:
        """Dispatch to add_text."""
        return self.add_text(**kwargs)

    def add_text(
        self,
        video_path: str,
        output_path: Optional[str] = None,
        # Timing
        start_time: float = 0.0,
        end_time: Optional[float] = None,
        duration: Optional[float] = None,
        # Rich text segments
        text_segments: Optional[List[Dict[str, Any]]] = None,
        # Simple text mode
        text: Optional[str] = None,
        font: str = DEFAULT_FONT,
        font_size: int = DEFAULT_FONT_SIZE,
        color: str = DEFAULT_COLOR,
        # Positioning
        position: Union[str, Tuple[int, int], List[int]] = "center",
        anchor_point: str = "center",
        # Background box
        background_color: Optional[str] = None,
        background_opacity: float = 1.0,
        background_padding: int = 10,
        background_radius: int = 0,
        background_border_width: int = 0,
        background_border_color: str = "000000",
        # Animation
        animation_in: str = "fade",
        animation_out: str = "fade",
        animation_in_duration: float = 0.3,
        animation_out_duration: float = 0.3,
        # Typewriter
        typewriter_char_duration: float = 0.05,
        typewriter_cursor: bool = True,
        typewriter_cursor_blink_rate: float = 0.5,
        # Effects
        opacity: float = 1.0,
        shadow: Optional[Dict[str, Any]] = None,
        stroke: Optional[Dict[str, Any]] = None,
        # Layout
        max_width: Optional[int] = None,
        line_spacing: float = 1.2,
        alignment: str = "center",
        # Encoding
        codec: str = "libx264",
        preset: str = "medium",
        crf: int = 23,
    ) -> ToolResult:
        """Add text overlay to video, generating an ASS file and burning it in."""
        # ── Validate ────────────────────────────────────────────
        err = self.validate_file_exists(video_path)
        if err:
            return ToolResult.fail(err)

        if not text and not text_segments:
            return ToolResult.fail("Either 'text' or 'text_segments' is required")

        video_w, video_h = _get_video_dimensions(video_path)

        # Resolve timing
        if end_time is None and duration is not None:
            end_time = start_time + duration
        if end_time is None:
            try:
                import ffmpeg
                probe = ffmpeg.probe(video_path)
                end_time = float(probe["format"]["duration"])
            except Exception:
                end_time = start_time + 10.0

        # Build output path
        if not output_path:
            base = Path(video_path)
            output_path = str(base.parent / f"{base.stem}_text{base.suffix}")
        self.ensure_output_dir(output_path)

        # ── Generate ASS ────────────────────────────────────────
        ass_path = str(Path(output_path).with_suffix(".ass"))

        try:
            if animation_in == "typewriter" or (text_segments and any(
                s.get("animation") == "typewriter" for s in text_segments
            )):
                self._generate_typewriter_ass(
                    ass_path, video_w, video_h,
                    text=text, text_segments=text_segments,
                    start_time=start_time, end_time=end_time,
                    font=font, font_size=font_size, color=color,
                    position=position, anchor_point=anchor_point,
                    background_color=background_color,
                    background_opacity=background_opacity,
                    background_padding=background_padding,
                    opacity=opacity, shadow=shadow, stroke=stroke,
                    char_duration=typewriter_char_duration,
                    cursor=typewriter_cursor,
                    cursor_blink_rate=typewriter_cursor_blink_rate,
                    alignment=alignment,
                )
            else:
                self._generate_ass(
                    ass_path, video_w, video_h,
                    text=text, text_segments=text_segments,
                    start_time=start_time, end_time=end_time,
                    font=font, font_size=font_size, color=color,
                    position=position, anchor_point=anchor_point,
                    background_color=background_color,
                    background_opacity=background_opacity,
                    background_padding=background_padding,
                    opacity=opacity, shadow=shadow, stroke=stroke,
                    animation_in=animation_in, animation_out=animation_out,
                    animation_in_duration=animation_in_duration,
                    animation_out_duration=animation_out_duration,
                    alignment=alignment,
                )
        except Exception as e:
            return ToolResult.fail(f"Failed to generate ASS file: {e}")

        # ── Burn subtitles ──────────────────────────────────────
        try:
            self._burn_subtitles(
                video_path, ass_path, output_path,
                codec=codec, preset=preset, crf=crf,
            )
        except Exception as e:
            return ToolResult.fail(f"Failed to burn text overlay: {e}")

        return ToolResult.ok(
            data={"subtitle_file": ass_path, "output_path": output_path},
            artifacts={"video_file": output_path, "ass_file": ass_path},
        )

    # ── ASS generation helpers ──────────────────────────────────

    def _ass_header(self, w: int, h: int) -> str:
        return (
            "[Script Info]\n"
            "ScriptType: v4.00+\n"
            f"PlayResX: {w}\n"
            f"PlayResY: {h}\n"
            "WrapStyle: 0\n"
            "ScaledBorderAndShadow: yes\n\n"
        )

    def _ass_style(
        self,
        name: str = "TextOverlay",
        font: str = DEFAULT_FONT,
        font_size: int = DEFAULT_FONT_SIZE,
        color: str = DEFAULT_COLOR,
        alignment: int = 5,
        opacity: float = 1.0,
        shadow: Optional[Dict[str, Any]] = None,
        stroke: Optional[Dict[str, Any]] = None,
        background_color: Optional[str] = None,
        background_opacity: float = 1.0,
        background_padding: int = 0,
    ) -> str:
        primary = _hex_to_ass_alpha(color, opacity)
        outline_color = _hex_to_ass_alpha(
            stroke.get("color", "000000") if stroke else "000000",
            opacity,
        )
        shadow_color = _hex_to_ass_alpha(
            shadow.get("color", "000000") if shadow else "000000",
            shadow.get("opacity", 0.5) if shadow else 0.0,
        )
        border_style = 3 if background_color else 1
        outline_w = stroke.get("width", 0) if stroke else 0
        shadow_depth = 0
        if shadow:
            shadow_depth = max(abs(shadow.get("x", 0)), abs(shadow.get("y", 0)), shadow.get("blur", 2))

        back_col = _hex_to_ass_alpha(background_color or "000000", background_opacity if background_color else 0)

        return (
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: {name},{font},{font_size},{primary},{primary},"
            f"{outline_color},{back_col},"
            f"0,0,0,0,100,100,0,0,{border_style},{outline_w},{shadow_depth},"
            f"{alignment},20,20,{background_padding},1\n\n"
        )

    def _generate_ass(
        self,
        ass_path: str,
        video_w: int,
        video_h: int,
        text: Optional[str],
        text_segments: Optional[List[Dict[str, Any]]],
        start_time: float,
        end_time: float,
        font: str,
        font_size: int,
        color: str,
        position: Union[str, Tuple[int, int], List[int]],
        anchor_point: str,
        background_color: Optional[str],
        background_opacity: float,
        background_padding: int,
        opacity: float,
        shadow: Optional[Dict[str, Any]],
        stroke: Optional[Dict[str, Any]],
        animation_in: str,
        animation_out: str,
        animation_in_duration: float,
        animation_out_duration: float,
        alignment: str,
    ):
        align_map = {"left": 1, "center": 2, "right": 3}
        base_align = align_map.get(alignment, 2)

        # Adjust alignment for position
        pos_name = position if isinstance(position, str) else "center"
        ass_align = ASS_ALIGNMENT.get(pos_name, 5)

        header = self._ass_header(video_w, video_h)
        style = self._ass_style(
            font=font, font_size=font_size, color=color,
            alignment=ass_align, opacity=opacity,
            shadow=shadow, stroke=stroke,
            background_color=background_color,
            background_opacity=background_opacity,
            background_padding=background_padding,
        )

        events = "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"

        t_start = _ass_timestamp(start_time)
        t_end = _ass_timestamp(end_time)

        # Build animation override tags
        override_tags = ""
        fade_in_ms = int(animation_in_duration * 1000)
        fade_out_ms = int(animation_out_duration * 1000)

        if animation_in == "fade" or animation_out == "fade":
            fin = fade_in_ms if animation_in == "fade" else 0
            fout = fade_out_ms if animation_out == "fade" else 0
            override_tags += f"\\fad({fin},{fout})"

        if animation_in == "scale":
            override_tags += f"\\fscx10\\fscy10\\t(0,{fade_in_ms},\\fscx100\\fscy100)"
        elif animation_out == "scale":
            total_ms = int((end_time - start_time) * 1000)
            t_start_out = total_ms - fade_out_ms
            override_tags += f"\\t({t_start_out},{total_ms},\\fscx10\\fscy10)"

        # Position override for pixel coordinates
        pos_override = ""
        if not isinstance(position, str):
            px, py = int(position[0]), int(position[1])
            pos_override = f"\\pos({px},{py})"

        if text_segments:
            # Rich text with per-segment formatting
            line_parts = []
            for seg in text_segments:
                seg_text = seg.get("text", "")
                seg_override = ""
                if seg.get("font"):
                    seg_override += f"\\fn{seg['font']}"
                if seg.get("font_size"):
                    seg_override += f"\\fs{seg['font_size']}"
                if seg.get("color"):
                    seg_override += f"\\c{_hex_to_ass(seg['color'])}"
                if seg.get("bold"):
                    seg_override += "\\b1"
                if seg.get("italic"):
                    seg_override += "\\i1"
                if seg.get("underline"):
                    seg_override += "\\u1"

                if seg_override:
                    line_parts.append(f"{{{seg_override}}}{seg_text}")
                else:
                    line_parts.append(seg_text)

            full_text = "\\N".join(line_parts)
        else:
            full_text = text.replace("\n", "\\N") if text else ""

        all_tags = pos_override + override_tags
        if all_tags:
            full_text = f"{{{all_tags}}}{full_text}"

        events += f"Dialogue: 0,{t_start},{t_end},TextOverlay,,0,0,0,,{full_text}\n"

        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(header + style + events)

        logger.info(f"Generated text overlay ASS: {ass_path}")

    def _generate_typewriter_ass(
        self,
        ass_path: str,
        video_w: int,
        video_h: int,
        text: Optional[str],
        text_segments: Optional[List[Dict[str, Any]]],
        start_time: float,
        end_time: float,
        font: str,
        font_size: int,
        color: str,
        position: Union[str, Tuple[int, int], List[int]],
        anchor_point: str,
        background_color: Optional[str],
        background_opacity: float,
        background_padding: int,
        opacity: float,
        shadow: Optional[Dict[str, Any]],
        stroke: Optional[Dict[str, Any]],
        char_duration: float,
        cursor: bool,
        cursor_blink_rate: float,
        alignment: str,
    ):
        """Generate ASS with typewriter character reveal effect."""
        pos_name = position if isinstance(position, str) else "center"
        ass_align = ASS_ALIGNMENT.get(pos_name, 5)

        header = self._ass_header(video_w, video_h)
        style = self._ass_style(
            font=font, font_size=font_size, color=color,
            alignment=ass_align, opacity=opacity,
            shadow=shadow, stroke=stroke,
            background_color=background_color,
            background_opacity=background_opacity,
            background_padding=background_padding,
        )
        # Transparent style for invisible characters
        style += (
            "Style: Invisible,"
            f"{font},{font_size},"
            f"&H00000000&,&H00000000&,&H00000000&,&H00000000&,"
            "0,0,0,0,100,100,0,0,1,0,0,"
            f"{ass_align},20,20,{background_padding},1\n\n"
        )

        events = "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"

        full_text = ""
        if text_segments:
            full_text = "".join(s.get("text", "") for s in text_segments)
        elif text:
            full_text = text

        pos_override = ""
        if not isinstance(position, str):
            px, py = int(position[0]), int(position[1])
            pos_override = f"\\pos({px},{py})"

        # Generate progressive reveal: each character appears one at a time
        for i in range(len(full_text)):
            char_start = start_time + i * char_duration
            char_end = end_time
            if char_start >= end_time:
                break

            visible = full_text[: i + 1]
            invisible = full_text[i + 1:]

            visible_text = visible.replace("\n", "\\N")
            invisible_text = invisible.replace("\n", "\\N")

            # Show visible chars in normal style, rest in transparent
            line = ""
            if pos_override:
                line += f"{{{pos_override}}}"
            line += visible_text
            if invisible_text:
                line += f"{{\\alpha&HFF&}}{invisible_text}"

            events += (
                f"Dialogue: 0,"
                f"{_ass_timestamp(char_start)},"
                f"{_ass_timestamp(min(char_start + char_duration, end_time))},"
                f"TextOverlay,,0,0,0,,{line}\n"
            )

        # Hold final text until end
        final_hold_start = start_time + len(full_text) * char_duration
        if final_hold_start < end_time:
            final_text = full_text.replace("\n", "\\N")
            if pos_override:
                final_text = f"{{{pos_override}}}{final_text}"
            events += (
                f"Dialogue: 0,"
                f"{_ass_timestamp(final_hold_start)},"
                f"{_ass_timestamp(end_time)},"
                f"TextOverlay,,0,0,0,,{final_text}\n"
            )

        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(header + style + events)

        logger.info(f"Generated typewriter ASS: {ass_path}")

    def _burn_subtitles(
        self,
        video_path: str,
        ass_path: str,
        output_path: str,
        codec: str = "libx264",
        preset: str = "medium",
        crf: int = 23,
    ):
        """Burn ASS subtitles onto video using FFmpeg."""
        import subprocess

        # Escape path for FFmpeg subtitle filter (Windows backslashes and colons)
        escaped_ass = ass_path.replace("\\", "/").replace(":", "\\:")

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"ass='{escaped_ass}'",
            "-c:v", codec,
            "-preset", preset,
            "-crf", str(crf),
            "-c:a", "copy",
            output_path,
        ]

        logger.info(f"Burning subtitles: {' '.join(cmd)}")
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
        )

        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed: {result.stderr[-500:]}")

        logger.info(f"Text overlay rendered: {output_path}")
