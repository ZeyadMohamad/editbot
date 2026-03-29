"""
Image overlay tool for compositing images onto video.

Supports multiple images with individual timing, position, size, animation,
border, shadow, rounded corners, chroma key, and blend modes.
Uses FFmpeg overlay filter with filter_complex for batch processing.
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from tools.base_tool import BaseTool, ToolResult, register_tool
from core.logging import setup_logger

logger = setup_logger("image_overlay_tool")

NAMED_POSITIONS = {
    "center":        (0.5, 0.5),
    "top_left":      (0.05, 0.05),
    "top_center":    (0.5, 0.05),
    "top_right":     (0.95, 0.05),
    "middle_left":   (0.05, 0.5),
    "middle_right":  (0.95, 0.5),
    "bottom_left":   (0.05, 0.95),
    "bottom_center": (0.5, 0.95),
    "bottom_right":  (0.95, 0.95),
    "center_left":   (0.05, 0.5),
    "center_right":  (0.95, 0.5),
}


def _get_video_info(path: str) -> Dict[str, Any]:
    """Get video dimensions and duration."""
    try:
        import ffmpeg
        probe = ffmpeg.probe(path)
        vs = next((s for s in probe["streams"] if s["codec_type"] == "video"), None)
        w = int(vs["width"]) if vs else 1920
        h = int(vs["height"]) if vs else 1080
        dur = float(probe["format"].get("duration", 0))
        return {"width": w, "height": h, "duration": dur}
    except Exception:
        return {"width": 1920, "height": 1080, "duration": 0}


def _resolve_size(
    size: Union[str, Tuple[int, int], List[int], float, None],
    width: Optional[int],
    height: Optional[int],
    scale: float,
    video_w: int,
    video_h: int,
) -> Tuple[int, int]:
    """Resolve size specification to (w, h) pixels."""
    if width and height:
        return int(width * scale), int(height * scale)

    if isinstance(size, (list, tuple)) and len(size) == 2:
        return int(size[0] * scale), int(size[1] * scale)

    if isinstance(size, str) and size.endswith("%"):
        pct = float(size.rstrip("%")) / 100.0
        sw = int(video_w * pct * scale)
        return sw, -1  # -1 = auto height preserving aspect ratio

    if isinstance(size, (int, float)):
        frac = float(size)
        if frac <= 1.0:
            sw = int(video_w * frac * scale)
        else:
            sw = int(frac * scale)
        return sw, -1

    if width:
        return int(width * scale), -1
    if height:
        return -1, int(height * scale)

    return int(video_w * 0.25 * scale), -1  # default 25%


def _resolve_position(
    position: Union[str, Tuple[int, int], List[int], None],
    x: Optional[int],
    y: Optional[int],
    anchor: str,
    img_w_expr: str,
    img_h_expr: str,
    video_w: int,
    video_h: int,
) -> Tuple[str, str]:
    """Resolve position to FFmpeg overlay x/y expressions."""
    if x is not None and y is not None:
        # Center the overlay on the given pixel coordinate
        ox = f"({x}-{img_w_expr}/2)"
        oy = f"({y}-{img_h_expr}/2)"
        return ox, oy

    if isinstance(position, (list, tuple)) and len(position) == 2:
        px, py = int(position[0]), int(position[1])
        return f"({px}-{img_w_expr}/2)", f"({py}-{img_h_expr}/2)"

    if isinstance(position, str):
        frac = NAMED_POSITIONS.get(position, (0.5, 0.5))
        fx, fy = frac

        # Center the image on the position point
        if anchor == "center":
            ox = f"({video_w}*{fx}-{img_w_expr}/2)"
            oy = f"({video_h}*{fy}-{img_h_expr}/2)"
        elif anchor == "top_left":
            ox = f"({video_w}*{fx})"
            oy = f"({video_h}*{fy})"
        elif anchor == "top_right":
            ox = f"({video_w}*{fx}-{img_w_expr})"
            oy = f"({video_h}*{fy})"
        elif anchor == "bottom_left":
            ox = f"({video_w}*{fx})"
            oy = f"({video_h}*{fy}-{img_h_expr})"
        elif anchor == "bottom_right":
            ox = f"({video_w}*{fx}-{img_w_expr})"
            oy = f"({video_h}*{fy}-{img_h_expr})"
        else:
            ox = f"({video_w}*{fx}-{img_w_expr}/2)"
            oy = f"({video_h}*{fy}-{img_h_expr}/2)"
        return ox, oy

    # Default center
    return f"({video_w}/2-{img_w_expr}/2)", f"({video_h}/2-{img_h_expr}/2)"


def _build_enable_expr(start_time: float, end_time: Optional[float]) -> str:
    """Build FFmpeg enable expression for timed overlay."""
    if end_time is not None:
        return f"between(t,{start_time},{end_time})"
    return f"gte(t,{start_time})"


@register_tool
class ImageOverlayTool(BaseTool):
    """Add one or more images to video with full styling control."""

    tool_id = "image_overlay"
    tool_name = "Image Overlay"
    description = "Add images to video with position, animation, borders, and effects"
    category = "video"
    version = "1.0.0"

    def execute(self, **kwargs) -> ToolResult:
        if "images" in kwargs:
            return self.add_images(**kwargs)
        return self.add_image(**kwargs)

    def add_image(
        self,
        video_path: str,
        image_path: str,
        output_path: Optional[str] = None,
        start_time: float = 0.0,
        end_time: Optional[float] = None,
        duration: Optional[float] = None,
        **kwargs,
    ) -> ToolResult:
        """Convenience method: add a single image."""
        if end_time is None and duration is not None:
            end_time = start_time + duration

        image_spec = {
            "path": image_path,
            "start_time": start_time,
            "end_time": end_time,
            **kwargs,
        }

        return self.add_images(
            video_path=video_path,
            output_path=output_path,
            images=[image_spec],
        )

    def add_images(
        self,
        video_path: str,
        output_path: Optional[str] = None,
        images: Optional[List[Dict[str, Any]]] = None,
        # Encoding
        codec: str = "libx264",
        preset: str = "medium",
        crf: int = 23,
        **kwargs,
    ) -> ToolResult:
        """Add multiple images to video with individual control."""
        err = self.validate_file_exists(video_path)
        if err:
            return ToolResult.fail(err)

        if not images or len(images) == 0:
            return ToolResult.fail("No images specified")

        # Validate image paths
        for i, img in enumerate(images):
            p = img.get("path")
            if not p:
                return ToolResult.fail(f"Image {i} missing 'path'")
            err = self.validate_file_exists(p)
            if err:
                return ToolResult.fail(f"Image {i}: {err}")

        if not output_path:
            base = Path(video_path)
            output_path = str(base.parent / f"{base.stem}_overlay{base.suffix}")
        self.ensure_output_dir(output_path)

        vinfo = _get_video_info(video_path)
        video_w, video_h = vinfo["width"], vinfo["height"]
        video_dur = vinfo["duration"]

        try:
            self._apply_overlays(
                video_path, output_path, images,
                video_w, video_h, video_dur,
                codec, preset, crf,
            )
        except Exception as e:
            return ToolResult.fail(f"Failed to overlay images: {e}")

        return ToolResult.ok(
            data={"output_path": output_path, "image_count": len(images)},
            artifacts={"video_file": output_path},
        )

    def _apply_overlays(
        self,
        video_path: str,
        output_path: str,
        images: List[Dict[str, Any]],
        video_w: int,
        video_h: int,
        video_dur: float,
        codec: str,
        preset: str,
        crf: int,
    ):
        """Build and execute FFmpeg filter_complex for all image overlays."""
        input_args = ["-i", video_path]

        # Pre-compute timing and detect which images need looping
        img_timings = []
        for img in images:
            try:
                start = float(img.get("start_time", 0.0))
            except Exception:
                start = 0.0
            end = img.get("end_time")
            dur = img.get("duration")
            if end is not None:
                try:
                    end = float(end)
                except Exception:
                    end = None
            if dur is not None:
                try:
                    dur = float(dur)
                except Exception:
                    dur = None
            if end is None and dur is not None:
                end = start + dur
            if end is None:
                end = video_dur if video_dur > 0 else None
            display_dur = (end - start) if end is not None else (video_dur - start if video_dur > 0 else 10.0)
            if display_dur <= 0:
                display_dur = 0.001
            has_fade = img.get("animate_in") == "fade" or img.get("animate_out") == "fade"
            img_timings.append({"start": start, "end": end, "display_dur": display_dur, "has_fade": has_fade})

        for img, timing in zip(images, img_timings):
            # Static images need -loop 1 to create a multi-frame stream
            # so that fade filters have time progression to animate over
            if timing["has_fade"]:
                loop_dur = timing["display_dur"] + 1.0  # small buffer
                input_args.extend(["-loop", "1", "-t", f"{loop_dur:.3f}", "-i", img["path"]])
            else:
                input_args.extend(["-i", img["path"]])

        filter_parts = []
        prev_label = "0:v"  # Start with the base video

        for idx, img in enumerate(images):
            input_idx = idx + 1  # 0 is the video
            img_label = f"img{idx}"
            out_label = f"v{idx}"

            # ── Resolve timing ──────────────────────────────────
            start = img_timings[idx]["start"]
            end = img_timings[idx]["end"]

            # ── Resolve size ────────────────────────────────────
            sw, sh = _resolve_size(
                img.get("size"), img.get("width"), img.get("height"),
                img.get("scale", 1.0), video_w, video_h,
            )

            # Build scale filter for image
            if sh == -1:
                scale_expr = f"scale={sw}:-1"
            elif sw == -1:
                scale_expr = f"scale=-1:{sh}"
            else:
                scale_expr = f"scale={sw}:{sh}"

            # Image preprocessing chain
            img_filters = [scale_expr]

            # Format to support alpha channel
            img_filters.append("format=rgba")

            # Rotation
            rotation = img.get("rotation", 0.0)
            if rotation != 0:
                rad = rotation * math.pi / 180
                img_filters.append(f"rotate={rad}:fillcolor=none")

            # Opacity
            opacity = img.get("opacity", 1.0)
            if opacity < 1.0:
                alpha_val = opacity
                img_filters.append(
                    f"colorchannelmixer=aa={alpha_val}"
                )

            # Chroma key
            chroma = img.get("chroma_key")
            if chroma:
                ck_color = chroma.get("color", "00FF00")
                ck_thresh = chroma.get("threshold", 0.3)
                ck_smooth = chroma.get("smoothness", 0.1)
                # Convert color name to hex
                color_map = {"green": "00FF00", "blue": "0000FF"}
                ck_hex = color_map.get(ck_color.lower(), ck_color)
                img_filters.append(
                    f"chromakey=0x{ck_hex}:{ck_thresh}:{ck_smooth}"
                )

            # Border (pad with color)
            border = img.get("border")
            if border and border.get("width", 0) > 0:
                bw = border["width"]
                bc = border.get("color", "FFFFFF")
                img_filters.append(
                    f"pad=iw+{bw * 2}:ih+{bw * 2}:{bw}:{bw}:color=0x{bc}"
                )

            img_chain = ",".join(img_filters)

            # ── Fade-in / fade-out alpha transitions ───────────
            anim_in = str(img.get("animate_in", "none")).lower()
            anim_out = str(img.get("animate_out", "none")).lower()
            try:
                anim_in_dur = float(img.get("animate_in_duration", 0.5) or 0.5)
            except Exception:
                anim_in_dur = 0.5
            try:
                anim_out_dur = float(img.get("animate_out_duration", 0.5) or 0.5)
            except Exception:
                anim_out_dur = 0.5
            anim_in_dur = max(0.0, anim_in_dur)
            anim_out_dur = max(0.0, anim_out_dur)

            fade_filters = []
            if anim_in == "fade":
                fade_filters.append(f"fade=t=in:st=0:d={anim_in_dur}:alpha=1")
            if anim_out == "fade" and end is not None:
                fade_out_start = max(0, (end - start) - anim_out_dur)
                fade_filters.append(f"fade=t=out:st={fade_out_start}:d={anim_out_dur}:alpha=1")

            if fade_filters:
                img_chain += "," + ",".join(fade_filters)
            # Keep transition timing relative to the overlay clip, then shift
            # the clip into the base timeline at start_time.
            img_chain += f",setpts=PTS-STARTPTS+{start:.3f}/TB"

            filter_parts.append(f"[{input_idx}:v]{img_chain}[{img_label}]")

            # ── Resolve position ────────────────────────────────
            pos_x, pos_y = _resolve_position(
                img.get("position", "center"),
                img.get("x"), img.get("y"),
                img.get("anchor", "center"),
                f"overlay_w", f"overlay_h",
                video_w, video_h,
            )

            # ── Build overlay filter ────────────────────────────
            enable = _build_enable_expr(start, end)

            overlay_x = pos_x
            overlay_y = pos_y

            # Slide animations (adjust overlay x/y over time)
            if anim_in == "slide_up":
                overlay_y = f"if(lt(t-{start},{anim_in_dur}),{video_h}+({pos_y}-{video_h})*(t-{start})/{anim_in_dur},{pos_y})"
            elif anim_in == "slide_down":
                overlay_y = f"if(lt(t-{start},{anim_in_dur}),-overlay_h+({pos_y}+overlay_h)*(t-{start})/{anim_in_dur},{pos_y})"
            elif anim_in == "slide_left":
                overlay_x = f"if(lt(t-{start},{anim_in_dur}),{video_w}+({pos_x}-{video_w})*(t-{start})/{anim_in_dur},{pos_x})"
            elif anim_in == "slide_right":
                overlay_x = f"if(lt(t-{start},{anim_in_dur}),-overlay_w+({pos_x}+overlay_w)*(t-{start})/{anim_in_dur},{pos_x})"

            overlay_filter = (
                f"[{prev_label}][{img_label}]overlay="
                f"x='{overlay_x}':y='{overlay_y}'"
                f":enable='{enable}'"
                f"[{out_label}]"
            )
            filter_parts.append(overlay_filter)
            prev_label = out_label

        filter_complex = ";".join(filter_parts)

        cmd = [
            "ffmpeg", "-y",
            *input_args,
            "-filter_complex", filter_complex,
            "-map", f"[{prev_label}]",
            "-map", "0:a?",
            "-c:v", codec,
            "-preset", preset,
            "-crf", str(crf),
            "-c:a", "copy",
            output_path,
        ]

        logger.info(f"Image overlay: ffmpeg with {len(images)} image(s)")
        logger.debug(f"Filter complex: {filter_complex}")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg overlay failed: {result.stderr[-800:]}")

        logger.info(f"Image overlay rendered: {output_path}")
