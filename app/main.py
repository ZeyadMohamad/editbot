"""
EditBot CLI - Main entry point for video editing automation.
"""
import argparse
import sys
import os
import json
import re
import shutil
from pathlib import Path
from typing import Optional, Dict, Any

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Auto-configure FFmpeg PATH for Windows
def setup_ffmpeg():
    """Add FFmpeg to PATH if not already available"""
    if shutil.which("ffmpeg"):
        return True
    
    ffmpeg_paths = [
        r"C:\Users\zeyad\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0.1-full_build\bin",
        r"C:\ffmpeg\bin",
        r"D:\ffmpeg\bin",
    ]
    
    for ffmpeg_path in ffmpeg_paths:
        ffmpeg_exe = Path(ffmpeg_path) / "ffmpeg.exe"
        if ffmpeg_exe.exists():
            os.environ["PATH"] = ffmpeg_path + os.pathsep + os.environ.get("PATH", "")
            return True
    return False

setup_ffmpeg()

from core.logging import setup_logger
from core.schema import CaptionStyle

logger = setup_logger("editbot")

# Try to import colorama, fallback if not available
try:
    from colorama import init, Fore, Style
    init()
    # Add GRAY as alias for LIGHTBLACK_EX or WHITE
    if not hasattr(Fore, 'GRAY'):
        Fore.GRAY = Fore.LIGHTBLACK_EX if hasattr(Fore, 'LIGHTBLACK_EX') else Fore.WHITE
except ImportError:
    class Fore:
        CYAN = YELLOW = GREEN = RED = GRAY = WHITE = ""
    class Style:
        RESET_ALL = ""


def print_banner():
    """Print the EditBot banner"""
    banner = f"""
{Fore.CYAN}╔═══════════════════════════════════════════════════════════╗
║                                                             ║
║   ███████╗██████╗ ██╗████████╗██████╗  ██████╗ ████████╗    ║
║   ██╔════╝██╔══██╗██║╚══██╔══╝██╔══██╗██╔═══██╗╚══██╔══╝    ║
║   █████╗  ██║  ██║██║   ██║   ██████╔╝██║   ██║   ██║       ║
║   ██╔══╝  ██║  ██║██║   ██║   ██╔══██╗██║   ██║   ██║       ║
║   ███████╗██████╔╝██║   ██║   ██████╔╝╚██████╔╝   ██║       ║
║   ╚══════╝╚═════╝ ╚═╝   ╚═╝   ╚═════╝  ╚═════╝    ╚═╝       ║
║                                                             ║
║           AI-Powered Video Editing Assistant                ║
╚═══════════════════════════════════════════════════════════╝{Style.RESET_ALL}
"""
    print(banner)


SUPPORTED_VIDEO_EXTENSIONS = [
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", 
    ".webm", ".m4v", ".mpeg", ".mpg", ".3gp", ".ts", ".mts"
]


def validate_video(video_path: str) -> Path:
    """Validate video file exists and is supported"""
    # Clean the path: remove quotes and whitespace
    video_path = video_path.strip().strip('"').strip("'")
    path = Path(video_path)
    
    if not path.exists():
        print(f"{Fore.RED}Error: Video file not found: {video_path}{Style.RESET_ALL}")
        sys.exit(1)
    
    if path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
        print(f"{Fore.RED}Error: Unsupported video format: {path.suffix}{Style.RESET_ALL}")
        print(f"{Fore.GRAY}Supported formats: {', '.join(SUPPORTED_VIDEO_EXTENSIONS)}{Style.RESET_ALL}")
        sys.exit(1)
    
    return path


def interactive_mode():
    """Run EditBot in interactive mode"""
    print_banner()
    print(f"{Fore.GREEN}Interactive Mode{Style.RESET_ALL}")
    print(f"{Fore.GRAY}Type 'quit' or 'exit' to stop{Style.RESET_ALL}\n")
    
    # Get video path
    while True:
        video_input = input(f"{Fore.YELLOW}📹 Enter video path: {Style.RESET_ALL}").strip()
        
        if video_input.lower() in ['quit', 'exit']:
            print(f"{Fore.CYAN}Goodbye!{Style.RESET_ALL}")
            sys.exit(0)
        
        # Remove various quote styles that might be pasted
        video_input = video_input.strip('"').strip("'").strip('"').strip()
        
        if not video_input:
            print(f"{Fore.RED}Please enter a video path.{Style.RESET_ALL}\n")
            continue
        
        path = Path(video_input)
        if path.exists() and path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS:
            video_path = path
            break
        else:
            if not path.exists():
                print(f"{Fore.RED}File not found: {video_input}{Style.RESET_ALL}\n")
            else:
                print(f"{Fore.RED}Unsupported format: {path.suffix}{Style.RESET_ALL}\n")
    
    print(f"\n{Fore.GRAY}Example prompts:{Style.RESET_ALL}")
    print(f"  - Add yellow captions")
    print(f"  - Add white subtitles at the bottom")
    print(f"  - Caption this video with large bold red text\n")
    
    while True:
        prompt = input(f"{Fore.YELLOW}✏️  What do you want to do? {Style.RESET_ALL}").strip()
        
        if prompt.lower() in ['quit', 'exit']:
            print(f"{Fore.CYAN}Goodbye!{Style.RESET_ALL}")
            sys.exit(0)
        
        if prompt:
            break
        print(f"{Fore.RED}Please enter a prompt.{Style.RESET_ALL}")
    
    return video_path, prompt


def parse_style_from_prompt(prompt: str, config_loader) -> CaptionStyle:
    """Parse caption style from user prompt using config"""
    prompt_lower = prompt.lower()
    
    colors_config = config_loader.get_config("colors") or {}
    fonts_config = config_loader.get_config("fonts") or {}
    
    colors_raw = colors_config.get("colors", {})
    # Extract hex values from nested color dicts
    colors = {}
    for name, value in colors_raw.items():
        if isinstance(value, dict):
            colors[name] = value.get("hex", "FFFFFF")
        else:
            colors[name] = value
    
    fonts = fonts_config.get("available_fonts", ["Arial"])
    
    style = CaptionStyle(
        font="Arial",
        font_size=48,
        primary_color="FFFFFF",
        outline_color="000000",
        position="bottom",
        bold=False,
        italic=False,
        outline_width=2,
        shadow=1
    )
    
    # Parse font size
    size_match = re.search(r'font\s*(?:size)?\s*(\d+)', prompt_lower)
    if size_match:
        style.font_size = int(size_match.group(1))
    elif "large" in prompt_lower or "big" in prompt_lower:
        style.font_size = 64
    elif "small" in prompt_lower or "tiny" in prompt_lower:
        style.font_size = 32
    elif "huge" in prompt_lower or "giant" in prompt_lower:
        style.font_size = 80
    
    # Parse position
    if "top" in prompt_lower:
        style.position = "top"
    elif "middle" in prompt_lower or "center" in prompt_lower:
        style.position = "middle"
    else:
        style.position = "bottom"
    
    # Parse caption text color (primary color)
    color_keywords = {
        "white": colors.get("white", "FFFFFF"),
        "red": colors.get("red", "0000FF"),
        "yellow": colors.get("yellow", "00FFFF"),
        "cyan": colors.get("cyan", "FFFF00"),
        "green": colors.get("green", "00FF00"),
        "blue": colors.get("blue", "FF0000"),
        "orange": colors.get("orange", "0080FF"),
        "purple": colors.get("purple", "FF0080"),
        "pink": colors.get("pink", "FF80FF"),
        "black": colors.get("black", "000000"),
    }
    
    # Check for text color patterns
    for color_name, color_hex in color_keywords.items():
        # Patterns: "red captions", "yellow text", "cyan subtitles", "make it blue", etc.
        if re.search(rf'\b{color_name}\b.*(caption|text|subtitle|color)', prompt_lower) or \
           re.search(rf'(caption|text|subtitle|color).{{0,15}}\b{color_name}\b', prompt_lower):
            style.primary_color = color_hex
            break
    
    # Parse font
    for font in fonts:
        if font.lower() in prompt_lower:
            style.font = font
            break
    
    # Parse text styles
    if "bold" in prompt_lower:
        style.bold = True
    if "italic" in prompt_lower:
        style.italic = True
    
    return style


def run_caption_pipeline(
    video_path: Path, 
    prompt: str, 
    output_dir: Optional[str] = None
) -> Dict[str, Any]:
    """Run the full captioning pipeline."""
    from tools.ffmpeg_tool import FFmpegTool
    from tools.whisperx_tool import WhisperXTool
    from tools.captions_tool import CaptionsTool
    from core.config_loader import ConfigLoader
    
    workspace = Path(output_dir) if output_dir else PROJECT_ROOT / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    
    video_name = video_path.stem
    
    print(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    print(f"{Fore.GREEN}Processing: {video_path.name}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    
    ffmpeg = FFmpegTool()
    # Use int8_float16 quantization for medium model - fits well in 4GB VRAM on GPU
    whisper_tool = WhisperXTool(model_size="medium", device="cuda", compute_type="int8_float16")
    captions = CaptionsTool()
    
    config_loader = ConfigLoader()
    style_config = parse_style_from_prompt(prompt, config_loader)
    
    print(f"\n{Fore.GRAY}Detected style:{Style.RESET_ALL}")
    print(f"  Font: {style_config.font} ({style_config.font_size}px)")
    print(f"  Position: {style_config.position}")
    print(f"  Text color: {style_config.primary_color}")
    
    results = {
        "success": False,
        "video_path": str(video_path),
        "prompt": prompt,
        "outputs": {},
        "errors": []
    }
    
    try:
        # Step 1: Extract audio
        print(f"\n{Fore.YELLOW}[1/4] 🎵 Extracting audio...{Style.RESET_ALL}")
        audio_path = workspace / f"{video_name}_audio.wav"
        
        audio_result = ffmpeg.extract_audio(str(video_path), str(audio_path))
        
        if not audio_result.get("success"):
            results["errors"].append(f"Audio extraction failed: {audio_result.get('error')}")
            print(f"{Fore.RED}✗ Failed to extract audio{Style.RESET_ALL}")
            return results
        
        print(f"{Fore.GREEN}✓ Audio extracted: {audio_path.name}{Style.RESET_ALL}")
        results["outputs"]["audio"] = str(audio_path)
        
        # Step 2: Transcribe
        print(f"\n{Fore.YELLOW}[2/4] 🎤 Transcribing speech...{Style.RESET_ALL}")
        print(f"{Fore.GRAY}  (First run downloads ~150MB model){Style.RESET_ALL}")
        
        transcription = whisper_tool.transcribe_and_align(str(audio_path))
        
        if not transcription.get("success"):
            results["errors"].append(f"Transcription failed: {transcription.get('error')}")
            print(f"{Fore.RED}✗ Failed to transcribe{Style.RESET_ALL}")
            return results
        
        words = transcription.get("words", [])
        language = transcription.get("language", "unknown")
        print(f"{Fore.GREEN}✓ Transcribed {len(words)} words (Language: {language}){Style.RESET_ALL}")
        
        transcript_path = workspace / f"{video_name}_transcript.json"
        with open(transcript_path, 'w', encoding='utf-8') as f:
            json.dump(transcription, f, indent=2, ensure_ascii=False)
        results["outputs"]["transcript"] = str(transcript_path)
        
        # Step 3: Generate captions
        print(f"\n{Fore.YELLOW}[3/4] 📝 Generating captions...{Style.RESET_ALL}")
        
        ass_path = workspace / f"{video_name}_captions.ass"
        
        video_info = ffmpeg.get_video_info(str(video_path))
        width = video_info.get("width", 1920)
        height = video_info.get("height", 1080)
        
        caption_result = captions.generate_ass_file(
            words=words,
            output_path=str(ass_path),
            style=style_config,
            video_width=width,
            video_height=height
        )
        
        if not caption_result.get("success"):
            results["errors"].append(f"Caption generation failed: {caption_result.get('error')}")
            print(f"{Fore.RED}✗ Failed to generate captions{Style.RESET_ALL}")
            return results
        
        print(f"{Fore.GREEN}✓ Generated {caption_result.get('total_lines', 0)} caption lines{Style.RESET_ALL}")
        results["outputs"]["subtitles"] = str(ass_path)
        
        # Step 4: Render video with subtitles
        print(f"\n{Fore.YELLOW}[4/4] 🎬 Rendering final video...{Style.RESET_ALL}")
        
        output_video = workspace / f"{video_name}_captioned.mp4"
        
        render_result = ffmpeg.render_subtitles(
            video_path=str(video_path),
            subtitle_path=str(ass_path),
            output_path=str(output_video)
        )
        
        if not render_result.get("success"):
            results["errors"].append(f"Render failed: {render_result.get('error')}")
            print(f"{Fore.RED}✗ Failed to render video{Style.RESET_ALL}")
            return results
        
        print(f"{Fore.GREEN}✓ Video rendered successfully{Style.RESET_ALL}")
        results["outputs"]["video"] = str(output_video)
        results["success"] = True
        
    except Exception as e:
        results["errors"].append(str(e))
        print(f"{Fore.RED}✗ Error: {e}{Style.RESET_ALL}")
        logger.exception("Pipeline error")
    
    return results


def process_video(video_path: Path, prompt: str, output_dir: str = None) -> Dict[str, Any]:
    """Process video with the given prompt"""
    
    result = run_caption_pipeline(video_path, prompt, output_dir)
    
    print(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    
    if result["success"]:
        print(f"{Fore.GREEN}✅ Processing Complete!{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
        
        print(f"\n{Fore.GREEN}Generated files:{Style.RESET_ALL}")
        for name, path in result["outputs"].items():
            print(f"  📄 {name}: {path}")
        
        if result["outputs"].get("video"):
            print(f"\n{Fore.CYAN}🎬 Output video: {result['outputs']['video']}{Style.RESET_ALL}")
    else:
        print(f"{Fore.RED}❌ Processing Failed{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
        
        if result["errors"]:
            print(f"\n{Fore.RED}Errors:{Style.RESET_ALL}")
            for error in result["errors"]:
                print(f"  - {error}")
    
    return result


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="EditBot - AI-Powered Video Editing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m app.main --video input.mp4 --prompt "Add yellow captions"
  python -m app.main --interactive
  python -m app.main -v video.mp4 -p "Add captions with red highlight" -o ./output
        """
    )
    
    parser.add_argument("-v", "--video", type=str, help="Path to input video file")
    parser.add_argument("-p", "--prompt", type=str, help="Editing prompt")
    parser.add_argument("-o", "--output", type=str, default=None, help="Output directory")
    parser.add_argument("-i", "--interactive", action="store_true", help="Interactive mode")
    
    args = parser.parse_args()
    
    if args.interactive or (not args.video and not args.prompt):
        video_path, prompt = interactive_mode()
    else:
        if not args.video:
            parser.error("--video is required (or use --interactive)")
        if not args.prompt:
            parser.error("--prompt is required (or use --interactive)")
        
        video_path = validate_video(args.video)
        prompt = args.prompt
    
    result = process_video(video_path, prompt, args.output)
    
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
