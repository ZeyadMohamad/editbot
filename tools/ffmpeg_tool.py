"""
FFmpeg tool for video/audio processing.
Supports multiple video formats and provides audio extraction, video info, and subtitle rendering.
"""
import json
import re
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import ffmpeg

from core.logging import setup_logger
from tools.base_tool import BaseTool, ToolResult, register_tool

logger = setup_logger("ffmpeg_tool")

# Supported formats (can be extended via config)
SUPPORTED_VIDEO_EXTENSIONS = [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpeg", ".mpg", ".3gp", ".ts", ".mts"]
SUPPORTED_AUDIO_EXTENSIONS = [".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".wma"]
IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp", ".wepb", ".bmp", ".gif", ".tiff"]

TRANSITIONS_CONFIG_NAME = "transitions.json"
_TRANSITIONS_CACHE: Optional[Dict[str, Any]] = None
_TRANSITION_MAP_CACHE: Optional[Tuple[Dict[str, str], Dict[str, Optional[float]], List[str]]] = None

_TRANSITION_SYNONYMS = {
    "crossfade": "fade",
    "cross fade": "fade",
    "fade to black": "fadeblack",
    "fade to white": "fadewhite",
    "fade to gray": "fadegrays",
    "dip to black": "fadeblack",
    "dip to white": "fadewhite"
}

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

    fps = None
    if video_stream:
        fps_str = video_stream.get("r_frame_rate") or video_stream.get("avg_frame_rate") or "0/0"
        try:
            if "/" in fps_str:
                num, den = fps_str.split("/")
                fps = float(num) / float(den) if float(den) != 0 else None
            else:
                fps = float(fps_str)
        except Exception:
            fps = None

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
        "fps": fps or 0.0,
        "has_audio": audio_stream is not None,
        "audio_sample_rate": sample_rate,
        "audio_channel_layout": channel_layout or "stereo"
    }


def _is_image_path(path: str) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def _load_transitions_config() -> Dict[str, Any]:
    global _TRANSITIONS_CACHE
    if _TRANSITIONS_CACHE is not None:
        return _TRANSITIONS_CACHE
    config_path = Path(__file__).parent.parent / "configs" / TRANSITIONS_CONFIG_NAME
    if not config_path.exists():
        _TRANSITIONS_CACHE = {}
        return _TRANSITIONS_CACHE
    with open(config_path, "r", encoding="utf-8") as f:
        _TRANSITIONS_CACHE = json.load(f)
    return _TRANSITIONS_CACHE


def _normalize_transition_key(value: str) -> str:
    text = (value or "").lower().strip()
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"[^a-z0-9 ]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _get_transition_maps() -> Tuple[Dict[str, str], Dict[str, Optional[float]], List[str]]:
    global _TRANSITION_MAP_CACHE
    if _TRANSITION_MAP_CACHE is not None:
        return _TRANSITION_MAP_CACHE

    config = _load_transitions_config()
    transitions = config.get("transitions", [])
    name_map: Dict[str, str] = {}
    defaults: Dict[str, Optional[float]] = {}
    names: List[str] = []

    for item in transitions:
        name = item.get("name")
        code = item.get("code")
        if not code:
            continue
        defaults[code] = item.get("default_duration")
        if name:
            names.append(name)
            normalized = _normalize_transition_key(name)
            if normalized:
                name_map[normalized] = code
                name_map[normalized.replace(" ", "")] = code

    _TRANSITION_MAP_CACHE = (name_map, defaults, names)
    return _TRANSITION_MAP_CACHE


@register_tool
class FFmpegTool(BaseTool):
    """Handles FFmpeg operations for video and audio processing"""
    
    # Tool metadata
    tool_id = "ffmpeg"
    tool_name = "FFmpeg Tool"
    description = "Video and audio processing using FFmpeg"
    category = "media"
    version = "1.0.0"
    
    def __init__(self, ffmpeg_path: Optional[str] = None):
        super().__init__()
        self.ffmpeg_path = ffmpeg_path or "ffmpeg"
    
    def execute(self, operation: str, **kwargs) -> ToolResult:
        """Generic execute method - routes to specific operations"""
        operations = {
            "extract_audio": self.extract_audio,
            "get_video_info": self.get_video_info,
            "render_subtitles": self.render_subtitles,
            "apply_transitions": self.apply_transitions
        }
        
        if operation not in operations:
            return ToolResult.fail(f"Unknown operation: {operation}")
        
        result = operations[operation](**kwargs)
        
        if isinstance(result, dict):
            if result.get("success"):
                return ToolResult.ok(data=result)
            else:
                return ToolResult.fail(result.get("error", "Unknown error"))
        return result
    
    def _validate_video_path(self, video_path: str) -> Optional[str]:
        """Validate video file exists and has supported extension"""
        error = self.validate_file_exists(video_path)
        if error:
            return error
        
        error = self.validate_file_extension(video_path, SUPPORTED_VIDEO_EXTENSIONS)
        if error:
            return error
        
        return None

    def extract_audio(
        self, 
        video_path: str, 
        output_path: str, 
        sample_rate: int = 16000,
        audio_format: str = "wav"
    ) -> Dict[str, Any]:
        """
        Extract audio from video file.
        
        Args:
            video_path: Path to input video (supports multiple formats)
            output_path: Path for output audio file
            sample_rate: Audio sample rate (default 16000 for Whisper)
            audio_format: Output format (wav, mp3, etc.)
        
        Returns:
            Dictionary with result info
        """
        self.logger.info(f"Extracting audio from {video_path}")
        
        # Validate input
        error = self._validate_video_path(video_path)
        if error:
            return {"success": False, "error": error}
        
        try:
            # Ensure output directory exists
            self.ensure_output_dir(output_path)
            
            # Extract audio using ffmpeg-python
            stream = ffmpeg.input(video_path)
            stream = ffmpeg.output(
                stream, 
                output_path,
                acodec='pcm_s16le' if audio_format == 'wav' else 'libmp3lame',
                ac=1,  # mono
                ar=sample_rate
            )
            ffmpeg.run(stream, overwrite_output=True, capture_stdout=True, capture_stderr=True)
            
            self.logger.info(f"Audio extracted successfully to {output_path}")
            return {
                "success": True,
                "output_path": output_path,
                "audio_file": output_path,  # Alias for orchestrator
                "sample_rate": sample_rate,
                "format": audio_format
            }
            
        except ffmpeg.Error as e:
            error_msg = e.stderr.decode() if e.stderr else str(e)
            self.logger.error(f"FFmpeg error: {error_msg}")
            return {
                "success": False,
                "error": error_msg
            }
        except Exception as e:
            self.logger.error(f"Error extracting audio: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_video_info(self, video_path: str) -> Dict[str, Any]:
        """Get video metadata including duration, resolution, fps"""
        # Validate input
        error = self._validate_video_path(video_path)
        if error:
            return {"success": False, "error": error}
        
        try:
            probe = ffmpeg.probe(video_path)
            video_info = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
            audio_info = next((s for s in probe['streams'] if s['codec_type'] == 'audio'), None)
            
            if not video_info:
                return {"success": False, "error": "No video stream found"}
            
            # Safely parse fps
            fps_str = video_info.get('r_frame_rate', '30/1')
            try:
                if '/' in fps_str:
                    num, den = fps_str.split('/')
                    fps = float(num) / float(den) if float(den) != 0 else 30.0
                else:
                    fps = float(fps_str)
            except:
                fps = 30.0
            
            return {
                "success": True,
                "duration": float(probe['format'].get('duration', 0)),
                "width": int(video_info.get('width', 0)),
                "height": int(video_info.get('height', 0)),
                "fps": fps,
                "has_audio": audio_info is not None,
                "format": probe['format'].get('format_name', 'unknown')
            }
        except Exception as e:
            self.logger.error(f"Error getting video info: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def render_subtitles(
        self, 
        video_path: str, 
        subtitle_path: str, 
        output_path: str,
        codec: str = "libx264",
        preset: str = "medium",
        crf: int = 23
    ) -> Dict[str, Any]:
        """
        Burn subtitles into video
        
        Args:
            video_path: Input video path
            subtitle_path: ASS subtitle file path
            output_path: Output video path
            codec: Video codec (default libx264)
            preset: Encoding preset (ultrafast, fast, medium, slow)
            crf: Constant Rate Factor (0-51, lower is better quality)
        
        Returns:
            Dictionary with result info
        """
        self.logger.info(f"Rendering subtitles onto video")
        
        # Validate inputs
        error = self._validate_video_path(video_path)
        if error:
            return {"success": False, "error": error}
        
        error = self.validate_file_exists(subtitle_path)
        if error:
            return {"success": False, "error": error}
        
        try:
            self.ensure_output_dir(output_path)
            
            # Convert Windows paths for ffmpeg filter (escape colons and backslashes)
            subtitle_path_fixed = subtitle_path.replace('\\', '/').replace(':', '\\:')
            
            # Build ffmpeg command
            stream = ffmpeg.input(video_path)
            stream = ffmpeg.output(
                stream,
                output_path,
                vf=f"ass='{subtitle_path_fixed}'",
                vcodec=codec,
                preset=preset,
                crf=crf,
                pix_fmt="yuv420p",
                acodec='copy'  # Copy audio stream
            )
            
            ffmpeg.run(stream, overwrite_output=True, capture_stdout=True, capture_stderr=True)
            
            self.logger.info(f"Video with subtitles saved to {output_path}")
            return {
                "success": True,
                "output_path": output_path,
                "video_file": output_path  # Alias for orchestrator
            }
            
        except ffmpeg.Error as e:
            error_msg = e.stderr.decode() if e.stderr else str(e)
            self.logger.error(f"FFmpeg error: {error_msg}")
            return {
                "success": False,
                "error": error_msg
            }
        except Exception as e:
            self.logger.error(f"Error rendering subtitles: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }

    def apply_transitions(
        self,
        clips: Optional[List[Any]] = None,
        output_path: Optional[str] = None,
        transitions: Optional[List[Any]] = None,
        transition_duration: Optional[float] = 1.0,
        codec: str = "libx264",
        preset: str = "medium",
        crf: int = 23,
        source_path: Optional[str] = None,
        segments: Optional[List[Any]] = None
    ) -> Dict[str, Any]:
        """
        Apply FFmpeg xfade transitions between multiple clips.

        Args:
            clips: Ordered list of clip paths or dicts with path/start/end/duration.
            segments: Optional list of segment dicts (start/end/duration) for a single source clip.
            source_path: Optional default path to use for segments or clips missing a path.
            output_path: Output video path.
            transitions: Optional list of transitions between clips.
            transition_duration: Default transition duration (seconds).
            codec: Video codec.
            preset: Encoding preset.
            crf: Constant Rate Factor.
        """
        if not output_path:
            return {"success": False, "error": "Missing output_path for transitions"}

        clip_items = clips or []
        if (not clip_items or len(clip_items) < 2) and segments and len(segments) >= 2:
            clip_items = segments

        if not clip_items or len(clip_items) < 2:
            return {
                "success": False,
                "error": "Provide at least two clips or two segments for transitions"
            }

        output_ext_error = self.validate_file_extension(output_path, SUPPORTED_VIDEO_EXTENSIONS)
        if output_ext_error:
            return {"success": False, "error": output_ext_error}

        config = _load_transitions_config()
        name_map, defaults_map, available_names = _get_transition_maps()
        config_default_duration = _parse_timecode_to_seconds(config.get("default_duration")) or 1.0
        default_duration = _parse_timecode_to_seconds(transition_duration)
        if default_duration is None or default_duration <= 0:
            default_duration = config_default_duration

        parsed_clips: List[Dict[str, Any]] = []
        for idx, clip in enumerate(clip_items):
            if isinstance(clip, str):
                clip_data = {"path": clip}
            elif isinstance(clip, dict):
                clip_data = clip
            else:
                return {"success": False, "error": f"Invalid clip at index {idx}"}

            path = (
                clip_data.get("path")
                or clip_data.get("video_path")
                or clip_data.get("file")
                or clip_data.get("source")
                or clip_data.get("source_path")
            )
            if not path and source_path:
                path = source_path
            if not path:
                return {"success": False, "error": f"Missing clip path at index {idx}"}

            path = str(path)
            error = self.validate_file_exists(path)
            if error:
                return {"success": False, "error": error}

            is_image = _is_image_path(path)
            if not is_image:
                error = self.validate_file_extension(path, SUPPORTED_VIDEO_EXTENSIONS)
                if error:
                    return {"success": False, "error": error}
            else:
                ext = Path(path).suffix.lower()
                if ext not in IMAGE_EXTENSIONS:
                    return {"success": False, "error": f"Unsupported image format: {ext}"}

            start_time = _parse_timecode_to_seconds(clip_data.get("start_time") or clip_data.get("start"))
            end_time = _parse_timecode_to_seconds(clip_data.get("end_time") or clip_data.get("end"))
            duration = _parse_timecode_to_seconds(clip_data.get("duration"))

            if start_time is None or is_image:
                start_time = 0.0
            if start_time < 0:
                start_time = 0.0

            probe = _probe_media(path)
            clip_duration = probe.get("duration", 0.0)
            if is_image:
                if duration is None or duration <= 0:
                    return {"success": False, "error": f"Image clip requires duration: {path}"}
                clip_duration = duration
            elif clip_duration <= 0:
                return {"success": False, "error": f"Could not determine duration for clip: {path}"}

            if start_time > clip_duration:
                return {"success": False, "error": f"Start time exceeds clip duration for {path}"}

            if end_time is not None and not is_image:
                if end_time < start_time:
                    return {"success": False, "error": f"End time before start time for {path}"}
                duration = end_time - start_time
            elif duration is None and not is_image:
                duration = clip_duration - start_time

            if duration is None or duration <= 0:
                return {"success": False, "error": f"Invalid duration for clip: {path}"}

            max_duration = clip_duration - start_time
            if duration > max_duration:
                self.logger.warning(
                    f"Clamping duration for {path} from {duration:.3f}s to {max_duration:.3f}s"
                )
                duration = max_duration

            parsed_clips.append({
                "path": path,
                "start_time": start_time,
                "duration": duration,
                "width": probe.get("width", 0),
                "height": probe.get("height", 0),
                "fps": probe.get("fps", 0.0),
                "has_audio": probe.get("has_audio", False),
                "audio_sample_rate": probe.get("audio_sample_rate"),
                "audio_channel_layout": probe.get("audio_channel_layout") or "stereo",
                "is_image": is_image
            })

        base_width = parsed_clips[0].get("width", 0)
        base_height = parsed_clips[0].get("height", 0)
        base_fps = parsed_clips[0].get("fps", 0.0) or 0.0
        if base_width <= 0 or base_height <= 0:
            return {"success": False, "error": "Could not determine base clip resolution"}
        if base_fps <= 0:
            base_fps = 30.0

        audio_present = any(c.get("has_audio") for c in parsed_clips)
        sample_rate = next((c.get("audio_sample_rate") for c in parsed_clips if c.get("audio_sample_rate")), None) or 44100
        channel_layout = next((c.get("audio_channel_layout") for c in parsed_clips if c.get("audio_channel_layout")), None) or "stereo"

        if transitions is None:
            transitions = []
        if isinstance(transitions, (str, dict)):
            transitions = [transitions]

        transition_count = len(parsed_clips) - 1
        if len(transitions) > transition_count:
            return {"success": False, "error": f"Too many transitions provided. Expected {transition_count}."}
        if len(transitions) == 0:
            transitions = [{"name": "Cross Dissolve"}] * transition_count
        elif len(transitions) == 1 and transition_count > 1:
            transitions = transitions * transition_count
        elif len(transitions) < transition_count:
            transitions = transitions + [transitions[-1]] * (transition_count - len(transitions))

        parsed_transitions: List[Dict[str, Any]] = []
        for idx, transition in enumerate(transitions):
            raw_name = None
            duration = None
            if isinstance(transition, dict):
                duration = _parse_timecode_to_seconds(transition.get("duration"))
                raw_name = (
                    transition.get("code")
                    or transition.get("name")
                    or transition.get("transition")
                    or transition.get("type")
                )
            else:
                raw_name = transition

            if raw_name is None:
                return {"success": False, "error": f"Missing transition name at index {idx}"}

            raw_name = str(raw_name).strip()
            code = None
            if raw_name in defaults_map:
                code = raw_name
            else:
                normalized = _normalize_transition_key(raw_name)
                code = name_map.get(normalized) or name_map.get(normalized.replace(" ", "")) or _TRANSITION_SYNONYMS.get(normalized)

            if not code:
                available_preview = ", ".join(available_names[:12]) if available_names else ""
                return {
                    "success": False,
                    "error": f"Unknown transition '{raw_name}'. Available examples: {available_preview}"
                }

            if duration is None:
                duration = defaults_map.get(code) or default_duration

            if duration is None or duration <= 0:
                return {"success": False, "error": f"Invalid transition duration for '{raw_name}'"}

            parsed_transitions.append({
                "code": code,
                "duration": duration
            })

        for i in range(transition_count):
            min_clip = min(parsed_clips[i]["duration"], parsed_clips[i + 1]["duration"])
            if parsed_transitions[i]["duration"] >= min_clip:
                adjusted = max(0.05, min_clip - 0.05)
                if adjusted <= 0:
                    return {
                        "success": False,
                        "error": f"Transition duration at index {i} is longer than adjacent clip duration"
                    }
                self.logger.warning(
                    f"Clamping transition duration at index {i} from {parsed_transitions[i]['duration']:.2f}s "
                    f"to {adjusted:.2f}s to fit clip length"
                )
                parsed_transitions[i]["duration"] = adjusted

        self.ensure_output_dir(output_path)

        input_args: List[str] = []
        for clip in parsed_clips:
            if clip.get("is_image"):
                input_args += ["-loop", "1", "-t", _fmt_time(clip["duration"]), "-i", clip["path"]]
                continue
            if clip["start_time"] and clip["start_time"] > 0:
                input_args += ["-ss", _fmt_time(clip["start_time"])]
            if clip["duration"] is not None:
                input_args += ["-t", _fmt_time(clip["duration"])]
            input_args += ["-i", clip["path"]]

        filter_parts: List[str] = []

        scale_pad = (
            f"scale={base_width}:{base_height}:force_original_aspect_ratio=decrease,"
            f"pad={base_width}:{base_height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
        )
        fps_part = f"fps={_fmt_time(base_fps)}," if base_fps and base_fps > 0 else ""

        for idx, clip in enumerate(parsed_clips):
            v_label = f"v{idx}"
            v_filter = (
                f"[{idx}:v]{scale_pad}{fps_part}format=yuv420p,setpts=PTS-STARTPTS[{v_label}]"
            )
            filter_parts.append(v_filter)

            if audio_present:
                a_label = f"a{idx}"
                if clip.get("has_audio"):
                    a_filter = (
                        f"[{idx}:a]atrim=0:{_fmt_time(clip['duration'])},"
                        f"aresample={sample_rate},aformat=channel_layouts={channel_layout},"
                        f"asetpts=PTS-STARTPTS[{a_label}]"
                    )
                else:
                    a_filter = (
                        f"anullsrc=channel_layout={channel_layout}:sample_rate={sample_rate},"
                        f"atrim=0:{_fmt_time(clip['duration'])},asetpts=PTS-STARTPTS[{a_label}]"
                    )
                filter_parts.append(a_filter)

        current_duration = parsed_clips[0]["duration"]
        v_prev = "v0"
        a_prev = "a0"

        for idx, transition in enumerate(parsed_transitions, start=1):
            t_duration = transition["duration"]
            offset = current_duration - t_duration
            v_out = f"v{idx}x"
            filter_parts.append(
                f"[{v_prev}][v{idx}]xfade=transition={transition['code']}:duration={_fmt_time(t_duration)}:offset={_fmt_time(offset)}[{v_out}]"
            )
            if audio_present:
                a_out = f"a{idx}x"
                filter_parts.append(
                    f"[{a_prev}][a{idx}]acrossfade=d={_fmt_time(t_duration)}[{a_out}]"
                )
                a_prev = a_out
            v_prev = v_out
            current_duration = current_duration + parsed_clips[idx]["duration"] - t_duration

        filter_complex = ";".join(filter_parts)

        cmd = [self.ffmpeg_path, "-y", *input_args, "-filter_complex", filter_complex, "-map", f"[{v_prev}]"]

        if audio_present:
            cmd += ["-map", f"[{a_prev}]", "-c:a", "aac"]
        else:
            cmd += ["-an"]

        cmd += [
            "-c:v", codec,
            "-pix_fmt", "yuv420p",
            "-profile:v", "main",
            "-preset", preset,
            "-crf", str(crf),
            "-movflags", "+faststart",
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {"success": False, "error": result.stderr or "ffmpeg transition failed"}

        return {
            "success": True,
            "output_path": output_path,
            "video_file": output_path,
            "transition_count": len(parsed_transitions)
        }
