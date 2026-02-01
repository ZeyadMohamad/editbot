"""
FFmpeg tool for video/audio processing.
Supports multiple video formats and provides audio extraction, video info, and subtitle rendering.
"""
import ffmpeg
from pathlib import Path
from typing import Optional, Dict, Any, List
from core.logging import setup_logger
from tools.base_tool import BaseTool, ToolResult, register_tool

logger = setup_logger("ffmpeg_tool")

# Supported formats (can be extended via config)
SUPPORTED_VIDEO_EXTENSIONS = [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpeg", ".mpg", ".3gp", ".ts", ".mts"]
SUPPORTED_AUDIO_EXTENSIONS = [".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".wma"]


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
            "render_subtitles": self.render_subtitles
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
