"""
Background audio tool for mixing music and sound effects into video.

Supports two modes:
  - INSERT: replace original audio with background audio for a duration
  - OVERLAY: mix background audio with original (with optional ducking)

Uses FFmpeg audio filters (amix, volume, afade, sidechaincompress).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from tools.base_tool import BaseTool, ToolResult, register_tool
from core.logging import setup_logger

logger = setup_logger("background_audio_tool")


def _get_media_duration(path: str) -> float:
    """Get duration of a media file in seconds."""
    try:
        import ffmpeg
        probe = ffmpeg.probe(path)
        return float(probe["format"]["duration"])
    except Exception:
        return 0.0


@register_tool
class BackgroundAudioTool(BaseTool):
    """Add background music or sound effects to video."""

    tool_id = "background_audio"
    tool_name = "Background Audio"
    description = "Add background music or sound effects with volume control, ducking, and fades"
    category = "audio"
    version = "1.0.0"

    def execute(self, **kwargs) -> ToolResult:
        op = kwargs.pop("operation", "add_background_audio")
        if op == "add_sound_effect":
            return self.add_sound_effect(**kwargs)
        return self.add_background_audio(**kwargs)

    def add_background_audio(
        self,
        video_path: str,
        audio_path: str,
        output_path: Optional[str] = None,
        # Mode
        mode: Literal["insert", "overlay"] = "overlay",
        # Video timing
        video_start_time: float = 0.0,
        video_end_time: Optional[float] = None,
        video_duration: Optional[float] = None,
        # Audio source timing
        audio_start_time: float = 0.0,
        audio_end_time: Optional[float] = None,
        audio_duration: Optional[float] = None,
        # Volume
        background_volume: float = 0.3,
        original_volume: float = 1.0,
        # Insert mode fades for original audio
        original_audio_fade_out: float = 0.5,
        original_audio_fade_in: float = 0.5,
        # Ducking (overlay mode)
        ducking: Optional[Dict[str, Any]] = None,
        # Background audio fades
        fade_in_duration: float = 1.0,
        fade_out_duration: float = 2.0,
        # Looping
        loop: bool = False,
        crossfade_loop: bool = True,
        # EQ
        highpass_filter: Optional[float] = None,
        lowpass_filter: Optional[float] = None,
        # Output
        codec: str = "aac",
        bitrate: str = "192k",
    ) -> ToolResult:
        """Add background audio to video with full mixing control."""
        err = self.validate_file_exists(video_path)
        if err:
            return ToolResult.fail(err)
        err = self.validate_file_exists(audio_path)
        if err:
            return ToolResult.fail(err)

        if not output_path:
            base = Path(video_path)
            output_path = str(base.parent / f"{base.stem}_audio{base.suffix}")
        self.ensure_output_dir(output_path)

        video_dur = _get_media_duration(video_path)
        audio_dur = _get_media_duration(audio_path)

        # Resolve video timing
        if video_end_time is None and video_duration is not None:
            video_end_time = video_start_time + video_duration
        if video_end_time is None:
            video_end_time = video_dur

        # Resolve audio source timing
        if audio_end_time is None and audio_duration is not None:
            audio_end_time = audio_start_time + audio_duration
        if audio_end_time is None:
            audio_end_time = audio_dur

        segment_duration = video_end_time - video_start_time
        audio_segment_duration = audio_end_time - audio_start_time

        try:
            if mode == "insert":
                self._apply_insert(
                    video_path, audio_path, output_path,
                    video_start_time, video_end_time, segment_duration,
                    audio_start_time, audio_end_time,
                    background_volume,
                    original_audio_fade_out, original_audio_fade_in,
                    fade_in_duration, fade_out_duration,
                    loop, audio_segment_duration,
                    highpass_filter, lowpass_filter,
                    codec, bitrate,
                )
            else:
                self._apply_overlay(
                    video_path, audio_path, output_path,
                    video_start_time, video_end_time, segment_duration,
                    audio_start_time, audio_end_time,
                    background_volume, original_volume,
                    fade_in_duration, fade_out_duration,
                    ducking,
                    loop, audio_segment_duration,
                    highpass_filter, lowpass_filter,
                    codec, bitrate,
                )
        except Exception as e:
            return ToolResult.fail(f"Failed to add background audio: {e}")

        return ToolResult.ok(
            data={"output_path": output_path, "mode": mode},
            artifacts={"video_file": output_path},
        )

    def add_sound_effect(
        self,
        video_path: str,
        effect_path: str,
        output_path: Optional[str] = None,
        at_time: float = 0.0,
        volume: float = 1.0,
        fade_in: float = 0.0,
        fade_out: float = 0.0,
    ) -> ToolResult:
        """Add a single sound effect at a specific timestamp."""
        err = self.validate_file_exists(video_path)
        if err:
            return ToolResult.fail(err)
        err = self.validate_file_exists(effect_path)
        if err:
            return ToolResult.fail(err)

        if not output_path:
            base = Path(video_path)
            output_path = str(base.parent / f"{base.stem}_sfx{base.suffix}")
        self.ensure_output_dir(output_path)

        effect_dur = _get_media_duration(effect_path)

        # Build filter: delay the effect to at_time, apply volume and fades, mix
        delay_ms = int(at_time * 1000)

        bg_filters = []
        bg_filters.append(f"volume={volume}")
        if fade_in > 0:
            bg_filters.append(f"afade=t=in:d={fade_in}")
        if fade_out > 0:
            bg_filters.append(f"afade=t=out:st={max(0, effect_dur - fade_out)}:d={fade_out}")
        bg_filters.append(f"adelay={delay_ms}|{delay_ms}")

        bg_chain = ",".join(bg_filters)

        filter_complex = (
            f"[1:a]{bg_chain}[sfx];"
            f"[0:a][sfx]amix=inputs=2:duration=first:dropout_transition=0[out]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", effect_path,
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[out]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            output_path,
        ]

        logger.info(f"Adding sound effect: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode != 0:
            return ToolResult.fail(f"FFmpeg failed: {result.stderr[-500:]}")

        return ToolResult.ok(
            data={"output_path": output_path},
            artifacts={"video_file": output_path},
        )

    # ── Internal implementation ─────────────────────────────────

    def _build_bg_audio_filters(
        self,
        audio_start: float,
        audio_end: float,
        segment_duration: float,
        background_volume: float,
        fade_in: float,
        fade_out: float,
        loop: bool,
        audio_segment_duration: float,
        highpass: Optional[float],
        lowpass: Optional[float],
        video_start: float,
    ) -> str:
        """Build the filter chain for the background audio stream."""
        filters = []

        # Trim the audio source to desired segment
        filters.append(f"atrim=start={audio_start}:end={audio_end}")
        filters.append("asetpts=PTS-STARTPTS")

        # Loop if needed
        if loop and audio_segment_duration < segment_duration:
            loop_count = int(segment_duration / audio_segment_duration) + 1
            filters.append(f"aloop=loop={loop_count}:size=2e+09")
            filters.append(f"atrim=0:{segment_duration}")
            filters.append("asetpts=PTS-STARTPTS")

        # Trim to exact segment duration
        filters.append(f"atrim=0:{segment_duration}")
        filters.append("asetpts=PTS-STARTPTS")

        # Volume
        filters.append(f"volume={background_volume}")

        # EQ
        if highpass:
            filters.append(f"highpass=f={highpass}")
        if lowpass:
            filters.append(f"lowpass=f={lowpass}")

        # Fades
        if fade_in > 0:
            filters.append(f"afade=t=in:d={fade_in}")
        if fade_out > 0:
            fade_start = max(0, segment_duration - fade_out)
            filters.append(f"afade=t=out:st={fade_start}:d={fade_out}")

        # Delay to match video_start_time position
        if video_start > 0:
            delay_ms = int(video_start * 1000)
            filters.append(f"adelay={delay_ms}|{delay_ms}")

        return ",".join(filters)

    def _apply_insert(
        self,
        video_path, audio_path, output_path,
        video_start, video_end, segment_duration,
        audio_start, audio_end,
        background_volume,
        orig_fade_out, orig_fade_in,
        fade_in, fade_out,
        loop, audio_segment_duration,
        highpass, lowpass,
        codec, bitrate,
    ):
        """Insert mode: mute original audio during segment, play background instead."""
        bg_chain = self._build_bg_audio_filters(
            audio_start, audio_end, segment_duration,
            background_volume, fade_in, fade_out,
            loop, audio_segment_duration, highpass, lowpass,
            video_start,
        )

        # Build volume expression to mute original during insert window
        # Use volume filter with enable option for precise timing
        orig_filters = []
        if orig_fade_out > 0:
            fade_start = max(0, video_start - orig_fade_out)
            orig_filters.append(
                f"afade=t=out:st={fade_start}:d={orig_fade_out}:enable='between(t,{fade_start},{video_start})'"
            )
        # Mute during insert
        orig_filters.append(
            f"volume=0:enable='between(t,{video_start},{video_end})'"
        )
        if orig_fade_in > 0:
            orig_filters.append(
                f"afade=t=in:st={video_end}:d={orig_fade_in}:enable='between(t,{video_end},{video_end + orig_fade_in})'"
            )

        orig_chain = ",".join(orig_filters) if orig_filters else "anull"

        filter_complex = (
            f"[0:a]{orig_chain}[orig];"
            f"[1:a]{bg_chain}[bg];"
            f"[orig][bg]amix=inputs=2:duration=first:dropout_transition=0[out]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[out]",
            "-c:v", "copy",
            "-c:a", codec, "-b:a", bitrate,
            output_path,
        ]

        logger.info(f"Insert mode audio: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg insert failed: {result.stderr[-500:]}")

    def _apply_overlay(
        self,
        video_path, audio_path, output_path,
        video_start, video_end, segment_duration,
        audio_start, audio_end,
        background_volume, original_volume,
        fade_in, fade_out,
        ducking,
        loop, audio_segment_duration,
        highpass, lowpass,
        codec, bitrate,
    ):
        """Overlay mode: mix background with original audio."""
        bg_chain = self._build_bg_audio_filters(
            audio_start, audio_end, segment_duration,
            background_volume, fade_in, fade_out,
            loop, audio_segment_duration, highpass, lowpass,
            video_start,
        )

        # Original volume adjustment
        orig_chain = f"volume={original_volume}" if original_volume != 1.0 else "anull"

        if ducking and ducking.get("enabled"):
            duck_vol = ducking.get("duck_to_volume", 0.1)
            attack = ducking.get("attack_time", 0.1)
            release = ducking.get("release_time", 0.5)
            threshold = ducking.get("speech_threshold_db", -30.0)

            # Use sidechaincompress: original speech controls background volume
            filter_complex = (
                f"[0:a]{orig_chain}[orig];"
                f"[1:a]{bg_chain}[bg];"
                f"[bg][orig]sidechaincompress="
                f"threshold={10 ** (threshold / 20):.6f}:"
                f"ratio=20:"
                f"attack={attack * 1000}:"
                f"release={release * 1000}:"
                f"level_sc=1[ducked];"
                f"[orig][ducked]amix=inputs=2:duration=first:dropout_transition=0[out]"
            )
        else:
            filter_complex = (
                f"[0:a]{orig_chain}[orig];"
                f"[1:a]{bg_chain}[bg];"
                f"[orig][bg]amix=inputs=2:duration=first:dropout_transition=0[out]"
            )

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[out]",
            "-c:v", "copy",
            "-c:a", codec, "-b:a", bitrate,
            output_path,
        ]

        logger.info(f"Overlay mode audio: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg overlay failed: {result.stderr[-500:]}")
