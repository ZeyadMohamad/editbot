"""
Video editing tools module.

This module registers all tools with the ToolRegistry when imported.
Import this module to make all tools available to the orchestrator.
"""

# Import base tool infrastructure
from tools.base_tool import BaseTool, ToolResult, ToolRegistry, register_tool

# Import all tools to trigger registration
from tools.ffmpeg_tool import FFmpegTool
from tools.whisperx_tool import WhisperXTool
from tools.captions_tool import CaptionsTool
from tools.silence_cutter_tool import SilenceCutterTool

# Export for easy access
__all__ = [
    "BaseTool",
    "ToolResult",
    "ToolRegistry",
    "register_tool",
    "FFmpegTool",
    "WhisperXTool",
    "CaptionsTool",
    "SilenceCutterTool",
]


def get_all_tools():
    """Get all registered tools"""
    return ToolRegistry.get_all_tools()


def get_tool(tool_id: str):
    """Get a specific tool by ID"""
    return ToolRegistry.get_tool(tool_id)
