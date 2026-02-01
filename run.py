#!/usr/bin/env python
"""
EditBot Quick Launcher
======================
Simple entry point that handles all setup automatically.

Usage:
    python run.py                           # Interactive mode
    python run.py video.mp4                 # Caption video with default prompt
    python run.py video.mp4 "your prompt"   # Caption video with custom prompt
"""
import os
import sys
from pathlib import Path

# Auto-configure FFmpeg PATH for Windows
FFMPEG_PATHS = [
    r"C:\Users\zeyad\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0.1-full_build\bin",
    r"C:\ffmpeg\bin",
    r"D:\ffmpeg\bin",
]

def setup_ffmpeg():
    """Add FFmpeg to PATH if not already available"""
    # Check if ffmpeg already in PATH
    try:
        import shutil
        if shutil.which("ffmpeg"):
            return True
    except:
        pass
    
    # Try to find and add FFmpeg
    for ffmpeg_path in FFMPEG_PATHS:
        ffmpeg_exe = Path(ffmpeg_path) / "ffmpeg.exe"
        if ffmpeg_exe.exists():
            os.environ["PATH"] = ffmpeg_path + os.pathsep + os.environ.get("PATH", "")
            return True
    
    # Search common locations
    search_paths = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
        Path("C:/Program Files"),
        Path("C:/Program Files (x86)"),
        Path("D:/"),
    ]
    
    for search_base in search_paths:
        if search_base.exists():
            for ffmpeg_exe in search_base.rglob("ffmpeg.exe"):
                ffmpeg_dir = str(ffmpeg_exe.parent)
                os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
                return True
    
    return False


def main():
    # Handle help
    if len(sys.argv) > 1 and sys.argv[1] in ["-h", "--help", "help", "?"]:
        print("""
╔═══════════════════════════════════════════════════════════╗
║                    EditBot Quick Launcher                  ║
╚═══════════════════════════════════════════════════════════╝

Usage:
  python run.py                              Interactive mode (asks for video & prompt)
  python run.py "video.mp4"                  Caption video with default prompt
  python run.py "video.mp4" "your prompt"    Caption video with custom prompt

Examples:
  python run.py "C:\\Videos\\myfile.mp4"
  python run.py "C:\\Videos\\myfile.mp4" "Add captions with yellow highlight at bottom"
  python run.py "C:\\Videos\\myfile.mp4" "Bold white text, highlight in red, font size 48"

Prompt keywords:
  Position:   top, middle, bottom (default)
  Colors:     red, yellow, cyan, green, blue, orange, purple, pink
  Font size:  "font 32", "size 48", "large", "small"
  Style:      bold, italic, karaoke, glow, pop
  
Output:
  All files are saved to: editbot/workspace/
""")
        sys.exit(0)
    
    # Setup FFmpeg
    if not setup_ffmpeg():
        print("⚠️  Warning: FFmpeg not found. Video rendering may fail.")
        print("   Install FFmpeg: winget install Gyan.FFmpeg")
    
    # Import and run main app
    from app.main import main as app_main, interactive_mode, validate_video, process_video
    
    args = sys.argv[1:]
    
    if len(args) == 0:
        # Interactive mode
        video_path, prompt = interactive_mode()
        result = process_video(video_path, prompt)
        
    elif len(args) == 1:
        # Just video path - use default prompt
        video_path = validate_video(args[0])
        prompt = "Add captions to this video and highlight each spoken word in red"
        print(f"\n📝 Using default prompt: \"{prompt}\"\n")
        result = process_video(video_path, prompt)
        
    elif len(args) >= 2:
        # Video path + prompt
        video_path = validate_video(args[0])
        prompt = " ".join(args[1:])
        result = process_video(video_path, prompt)
    
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
