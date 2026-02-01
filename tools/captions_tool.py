"""
Captions tool for generating ASS subtitle files.
"""
import re
from typing import List, Dict, Any, Optional
from pathlib import Path
from core.schema import CaptionStyle
from core.logging import setup_logger
from tools.base_tool import BaseTool, ToolResult, register_tool

logger = setup_logger("captions_tool")


@register_tool
class CaptionsTool(BaseTool):
    """Generates ASS subtitle files"""
    
    tool_id = "captions"
    tool_name = "Captions Tool"
    description = "Generate ASS subtitle files"
    category = "subtitles"
    version = "3.0.0"
    
    def __init__(self):
        super().__init__()
        self.logger.info("Captions tool initialized")
    
    def execute(self, operation: str, **kwargs) -> ToolResult:
        operations = {"generate_ass_file": self.generate_ass_file}
        if operation not in operations:
            return ToolResult.fail(f"Unknown operation: {operation}")
        result = operations[operation](**kwargs)
        if isinstance(result, dict):
            return ToolResult.ok(data=result) if result.get("success") else ToolResult.fail(result.get("error", "Unknown error"))
        return result
    
    def _format_timestamp(self, seconds: float) -> str:
        """Convert seconds to ASS timestamp format (h:mm:ss.cs)"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        centiseconds = int((seconds % 1) * 100)
        return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"
    
    def _group_words_into_lines(self, words: List[Dict[str, Any]], max_words: int = 6, max_duration: float = 4.0) -> List[List[Dict[str, Any]]]:
        """Group words into caption lines"""
        lines = []
        current_line = []
        line_start = None
        
        for word in words:
            if not current_line:
                line_start = word["start"]
            current_line.append(word)
            
            if len(current_line) >= max_words or (word["end"] - line_start) >= max_duration:
                lines.append(current_line)
                current_line = []
                line_start = None
        
        if current_line:
            lines.append(current_line)
        return lines
    
    def _extract_color_hex(self, color_value) -> str:
        """Extract hex color from various formats"""
        if isinstance(color_value, dict):
            return color_value.get("hex", "FFFFFF")
        elif isinstance(color_value, str):
            color = color_value.strip().lstrip("&H").lstrip("#")
            if len(color) == 8:
                color = color[2:]  # Remove alpha
            if len(color) == 6:
                try:
                    int(color, 16)
                    return color
                except:
                    pass
        return "FFFFFF"
    
    def generate_ass_file(
        self,
        words: List[Dict[str, Any]],
        output_path: str,
        style: CaptionStyle,
        video_width: int = 1920,
        video_height: int = 1080,
        max_words_per_line: int = 6
    ) -> Dict[str, Any]:
        """Generate ASS subtitle file with basic captions (no highlighting)"""
        self.logger.info(f"Generating ASS file with {len(words)} words")
        
        try:
            if not words:
                return {"success": False, "error": "No words provided"}
            
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            
            lines = self._group_words_into_lines(words, max_words_per_line)
            self.logger.info(f"Grouped into {len(lines)} caption lines")
            
            # Colors
            primary_color = self._extract_color_hex(style.primary_color)
            outline_color = self._extract_color_hex(style.outline_color)
            
            # Alignment
            align_map = {"bottom": 2, "middle": 5, "top": 8}
            alignment = align_map.get(style.position, 2)
            margin_v = 50 if style.position == "bottom" else (video_height // 2 if style.position == "middle" else 50)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                # Write header
                f.write(f"""[Script Info]
Title: Generated Subtitles
ScriptType: v4.00+
WrapStyle: 0
PlayResX: {video_width}
PlayResY: {video_height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
""")
                
                # Simple style with text color
                f.write(f"Style: Default,{style.font},{style.font_size},&H00{primary_color},&H00{primary_color},&H00{outline_color},&H00000000,{'-1' if style.bold else '0'},0,0,0,100,100,0,0,1,2,1,{alignment},30,30,{margin_v},1\n")
                
                f.write("\n[Events]\n")
                f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
                
                # Generate simple dialogue lines
                for line_words in lines:
                    line_start = line_words[0]["start"]
                    line_end = line_words[-1]["end"]
                    start_ts = self._format_timestamp(line_start)
                    end_ts = self._format_timestamp(line_end)
                    
                    full_text = " ".join([w["word"] for w in line_words])
                    f.write(f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{full_text}\n")
            
            self.logger.info(f"ASS file generated: {output_path}")
            return {
                "success": True,
                "output_path": output_path,
                "subtitle_file": output_path,
                "total_lines": len(lines),
                "total_words": len(words)
            }
            
        except Exception as e:
            self.logger.error(f"Error: {str(e)}")
            return {"success": False, "error": str(e)}
