"""
Image-to-video tool for creating videos from static images.

Converts a static image into a video of specified duration, with configurable
resolution, frame rate, and optional silent audio track. This enables workflows
where an image serves as a video canvas for overlaying text, images, and effects.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from tools.base_tool import BaseTool, ToolResult, register_tool
from core.logging import setup_logger

logger = setup_logger("image_to_video")

SUPPORTED_IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif",
}

SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm",
}


def _get_image_info(path: str) -> Dict[str, Any]:
    """Probe image dimensions using FFmpeg."""
    try:
        import ffmpeg
        probe = ffmpeg.probe(path)
        vs = next((s for s in probe["streams"] if s["codec_type"] == "video"), None)
        if vs:
            return {
                "width": int(vs.get("width", 0)),
                "height": int(vs.get("height", 0)),
            }
    except Exception:
        pass
    return {"width": 0, "height": 0}


@register_tool
class ImageToVideoTool(BaseTool):
    """Convert a static image into a video of specified duration."""

    tool_id = "image_to_video"
    tool_name = "Image to Video"
    description = "Convert a static image into a video with configurable duration and resolution"
    category = "video"
    version = "1.0.0"

    def execute(self, operation: str = "convert", **kwargs) -> ToolResult:
        operations = {
            "convert": self.convert,
        }
        if operation not in operations:
            return ToolResult.fail(f"Unknown operation: {operation}")
        return operations[operation](**kwargs)

    def convert(
        self,
        image_path: str,
        output_path: Optional[str] = None,
        duration: float = 10.0,
        width: Optional[int] = None,
        height: Optional[int] = None,
        fps: int = 30,
        add_silent_audio: bool = True,
        codec: str = "libx264",
        preset: str = "medium",
        crf: int = 23,
    ) -> ToolResult:
        """
        Convert a static image to a video file.

        Args:
            image_path: Path to the source image.
            output_path: Path for the output video. Auto-generated if None.
            duration: Video duration in seconds (default 10).
            width: Output width in pixels. None = use image width.
            height: Output height in pixels. None = use image height.
            fps: Frame rate (default 30).
            add_silent_audio: Add a silent audio track for compatibility.
            codec: Video codec (default libx264).
            preset: Encoding preset (default medium).
            crf: Quality factor (default 23).
        """
        err = self.validate_file_exists(image_path)
        if err:
            return ToolResult.fail(err)

        ext = Path(image_path).suffix.lower()
        if ext not in SUPPORTED_IMAGE_EXTENSIONS:
            return ToolResult.fail(f"Unsupported image format: {ext}")

        if duration <= 0:
            return ToolResult.fail(f"Duration must be positive, got {duration}")

        if not output_path:
            base = Path(image_path)
            output_path = str(base.parent / f"{base.stem}_video.mp4")

        out_ext = Path(output_path).suffix.lower()
        if out_ext not in SUPPORTED_VIDEO_EXTENSIONS:
            return ToolResult.fail(f"Unsupported output format: {out_ext}")

        self.ensure_output_dir(output_path)

        # Determine output resolution
        if width is None or height is None:
            info = _get_image_info(image_path)
            img_w, img_h = info["width"], info["height"]
            if img_w <= 0 or img_h <= 0:
                return ToolResult.fail("Could not determine image dimensions")
            if width is None:
                width = img_w
            if height is None:
                height = img_h

        # Ensure even dimensions (required by most codecs)
        width = width if width % 2 == 0 else width + 1
        height = height if height % 2 == 0 else height + 1

        try:
            self._run_conversion(
                image_path, output_path,
                duration, width, height, fps,
                add_silent_audio, codec, preset, crf,
            )
        except Exception as e:
            return ToolResult.fail(f"Image-to-video conversion failed: {e}")

        logger.info(f"Created video from image: {output_path} ({duration}s, {width}x{height})")
        return ToolResult.ok(
            data={
                "output_path": output_path,
                "video_file": output_path,
                "duration": duration,
                "width": width,
                "height": height,
            },
            artifacts={"video_file": output_path},
        )

    def _run_conversion(
        self,
        image_path: str,
        output_path: str,
        duration: float,
        width: int,
        height: int,
        fps: int,
        add_silent_audio: bool,
        codec: str,
        preset: str,
        crf: int,
    ):
        """Build and execute the FFmpeg command."""
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", image_path,
            "-t", f"{duration:.3f}",
        ]

        # Add silent audio track for compatibility with later audio mixing
        if add_silent_audio:
            cmd += [
                "-f", "lavfi",
                "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
                "-t", f"{duration:.3f}",
            ]

        # Video filters: scale + pixel format
        vf = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p"
        cmd += ["-vf", vf]

        cmd += [
            "-r", str(fps),
            "-c:v", codec,
            "-preset", preset,
            "-crf", str(crf),
            "-pix_fmt", "yuv420p",
        ]

        if add_silent_audio:
            cmd += ["-c:a", "aac", "-shortest"]

        cmd += ["-movflags", "+faststart", output_path]

        logger.debug(f"FFmpeg command: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-800:] if result.stderr else "FFmpeg failed")
