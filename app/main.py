"""
EditBot CLI - Main entry point for video editing automation.
"""
import argparse
import sys
import os
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

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

SUPPORTED_IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp", ".wepb", ".bmp", ".gif", ".tiff"]

def validate_media(media_path: str, prompt: Optional[str] = None) -> Path:
    """Validate input media file. Images are allowed for rotate-only requests."""
    # Clean the path: remove quotes and whitespace
    media_path = media_path.strip().strip('"').strip("'")
    path = Path(media_path)
    
    if not path.exists():
        print(f"{Fore.RED}Error: Media file not found: {media_path}{Style.RESET_ALL}")
        sys.exit(1)

    ext = path.suffix.lower()
    if ext in SUPPORTED_VIDEO_EXTENSIONS:
        return path

    if ext in SUPPORTED_IMAGE_EXTENSIONS:
        prompt_lower = (prompt or "").lower()
        if "rotate" in prompt_lower or "clockwise" in prompt_lower or "counterclockwise" in prompt_lower or "anticlockwise" in prompt_lower:
            return path
        print(f"{Fore.RED}Error: Image input is supported for rotate requests only.{Style.RESET_ALL}")
        sys.exit(1)

    print(f"{Fore.RED}Error: Unsupported media format: {path.suffix}{Style.RESET_ALL}")
    print(
        f"{Fore.GRAY}Supported video formats: {', '.join(SUPPORTED_VIDEO_EXTENSIONS)}\n"
        f"Supported image formats (rotate-only): {', '.join(SUPPORTED_IMAGE_EXTENSIONS)}{Style.RESET_ALL}"
    )
    sys.exit(1)

    return path

# Default slowdown speed for captions - 0.80 means 20% slower, 0.85 means 15% slower, etc.
CAPTION_AUDIO_SPEED = 0.85

def slow_audio(input_wav: Path, output_wav: Path, speed: float) -> None:
    if speed <= 0:
        raise ValueError("speed must be > 0")
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_wav),
        "-filter:a",
        f"atempo={speed}",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(output_wav),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg not found. Install ffmpeg or add it to PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"ffmpeg failed to slow audio: {exc.stderr.decode(errors='ignore')}"
        ) from exc


def _scale_timestamp(value: Any, factor: float) -> float:
    try:
        return float(value) * factor
    except Exception:
        return 0.0


def scale_transcription_timestamps(transcription: Dict[str, Any], factor: float) -> Dict[str, Any]:
    if not transcription or factor == 1.0:
        return transcription
    words = transcription.get("words", [])
    for word in words:
        word["start"] = _scale_timestamp(word.get("start", 0.0), factor)
        word["end"] = _scale_timestamp(word.get("end", 0.0), factor)

    segments = transcription.get("segments", [])
    for segment in segments:
        segment["start"] = _scale_timestamp(segment.get("start", 0.0), factor)
        segment["end"] = _scale_timestamp(segment.get("end", 0.0), factor)
        if "words" in segment:
            for word in segment["words"]:
                word["start"] = _scale_timestamp(word.get("start", 0.0), factor)
                word["end"] = _scale_timestamp(word.get("end", 0.0), factor)
    return transcription


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
        if path.exists() and path.suffix.lower() in (SUPPORTED_VIDEO_EXTENSIONS + SUPPORTED_IMAGE_EXTENSIONS):
            video_path = path
            break
        else:
            if not path.exists():
                print(f"{Fore.RED}File not found: {video_input}{Style.RESET_ALL}\n")
            else:
                print(f"{Fore.RED}Unsupported format: {path.suffix}{Style.RESET_ALL}\n")
    
    print(f"\n{Fore.GRAY}Example prompts:{Style.RESET_ALL}")
    print(f"  - Add yellow captions")
    print(f"  - Rotate right 2 times")
    print(f"  - Rotate 37.5 degrees clockwise")
    print(f"  - Add white subtitles at the bottom")
    print(f"  - Remove silence and filler words, then add captions")
    print(f"  - Cut from 18.005 to 18.670, then add captions")
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


def _get_color_hex_map(config_loader) -> Dict[str, str]:
    """Load color name -> ASS BGR hex from config."""
    colors_config = config_loader.get_config("colors") or {}
    colors_raw = colors_config.get("colors", {})
    colors: Dict[str, str] = {}
    for name, value in colors_raw.items():
        key = str(name).lower()
        if isinstance(value, dict):
            colors[key] = str(value.get("hex", "FFFFFF")).upper()
        else:
            colors[key] = str(value).upper()

    # Safe defaults
    colors.setdefault("yellow", "00FFFF")
    colors.setdefault("white", "FFFFFF")
    colors.setdefault("black", "000000")
    return colors


def parse_highlight_options_from_prompt(prompt: str, config_loader) -> Dict[str, Any]:
    """
    Parse caption highlight behavior from prompt.
    Returns options consumed by CaptionsTool.generate_ass_file(..., highlight_options=...).
    """
    prompt_lower = (prompt or "").lower()
    colors = _get_color_hex_map(config_loader)

    options: Dict[str, Any] = {
        "enabled": False,
        "force_disable": False,
        "highlight_type": "word_by_word",
        "highlight_color": colors.get("yellow", "00FFFF"),
        "progressive_color": colors.get("yellow", "00FFFF"),
        "current_word_bold": False,
        "current_word_box": False,
        "box_color": colors.get("black", "000000"),
        "box_alpha": "66",
    }

    # Explicit "off" always wins.
    if any(
        x in prompt_lower
        for x in [
            "no highlight",
            "no highlighting",
            "without highlight",
            "without highlighting",
            "don't highlight",
            "do not highlight",
            "plain subtitle",
            "plain subtitles",
            "plain caption",
            "plain captions",
        ]
    ):
        options["force_disable"] = True
        return options

    highlight_triggers = [
        "highlight",
        "karaoke",
        "word by word",
        "word-by-word",
        "current word",
        "progressive",
        "background box",
        "word box",
    ]

    if not any(x in prompt_lower for x in highlight_triggers):
        return options

    options["enabled"] = True

    if any(x in prompt_lower for x in ["progressive", "up to current", "until current", "whole sentence"]):
        options["highlight_type"] = "progressive"
    elif any(x in prompt_lower for x in ["word by word", "word-by-word", "one word at a time"]):
        options["highlight_type"] = "word_by_word"

    if any(x in prompt_lower for x in ["background box", "word box", "box behind", "highlight box"]):
        options["current_word_box"] = True

    if re.search(r"(current|spoken|active)\s+word.{0,20}\bbold\b|\bbold\b.{0,20}(current|spoken|active)\s+word", prompt_lower):
        options["current_word_bold"] = True

    for color_name, color_hex in colors.items():
        # highlight color
        if re.search(rf"(highlight|current word|spoken word).{{0,20}}\b{re.escape(color_name)}\b", prompt_lower) or \
           re.search(rf"\b{re.escape(color_name)}\b.{{0,20}}(highlight|current word|spoken word)", prompt_lower):
            options["highlight_color"] = color_hex

        # progressive color
        if re.search(rf"(progressive|sentence|past words).{{0,20}}\b{re.escape(color_name)}\b", prompt_lower) or \
           re.search(rf"\b{re.escape(color_name)}\b.{{0,20}}(progressive|sentence|past words)", prompt_lower):
            options["progressive_color"] = color_hex
            options["highlight_type"] = "progressive"

        # box color
        if re.search(rf"(box|background).{{0,20}}\b{re.escape(color_name)}\b", prompt_lower) or \
           re.search(rf"\b{re.escape(color_name)}\b.{{0,20}}(box|background)", prompt_lower):
            options["box_color"] = color_hex
            options["current_word_box"] = True

    if options["highlight_type"] != "progressive":
        options["progressive_color"] = options["highlight_color"]

    return options


TRANSITION_SYNONYMS = {
    "cross dissolve": "Cross Dissolve",
    "crossdissolve": "Cross Dissolve",
    "crossfade": "Cross Dissolve",
    "cross fade": "Cross Dissolve",
    "dip to black": "Dip to Black",
    "fade to black": "Dip to Black",
    "dip to white": "Dip to White",
    "fade to white": "Dip to White",
}

IMAGE_EXTENSIONS = list(SUPPORTED_IMAGE_EXTENSIONS)


def _parse_timecode_seconds(value: Any) -> Optional[float]:
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


def _normalize_stock_mode(value: Any) -> str:
    mode = (value or "overlay").strip().lower()
    if mode in ("overlay", "above", "layer", "over"):
        return "overlay"
    if mode in ("insert", "splice", "cutaway", "extension", "replace"):
        return "insert"
    return mode


def _is_image_path(path: str) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def rotation_mentioned(prompt: str) -> bool:
    prompt_lower = (prompt or "").lower()
    return any(tok in prompt_lower for tok in ["rotate", "rotation", "clockwise", "counterclockwise", "anticlockwise"])


def parse_rotation_from_prompt(prompt: str) -> Optional[float]:
    if not prompt or not rotation_mentioned(prompt):
        return None
    try:
        from tools.rotate_tool import parse_rotation_cw_degrees
        return parse_rotation_cw_degrees(prompt)
    except Exception:
        return None


def parse_transition_from_prompt(prompt: str, config_loader) -> Optional[Dict[str, Any]]:
    if not prompt:
        return None
    prompt_lower = prompt.lower()
    if not any(k in prompt_lower for k in ["transition", "cross dissolve", "crossfade", "xfade", "dip to black", "dip to white"]):
        return None

    transitions_config = config_loader.get_config("transitions") or {}
    transitions = transitions_config.get("transitions", [])

    name = None
    for item in transitions:
        candidate = item.get("name")
        if candidate and candidate.lower() in prompt_lower:
            name = candidate
            break

    if not name:
        for key, value in TRANSITION_SYNONYMS.items():
            if key in prompt_lower:
                name = value
                break

    if not name:
        name = "Cross Dissolve"

    duration = None
    duration_range_match = re.search(
        r"(?:transition(?:\s*duration|\s*length)?|duration|length)\s*"
        r"(?:is|=|:|of|for|from)?\s*"
        r"(?:duration\s*)?"
        r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds)?\s*"
        r"(?:to|-|–|—)\s*"
        r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds)?",
        prompt_lower
    )
    if duration_range_match:
        start_val = _parse_timecode_seconds(duration_range_match.group(1))
        end_val = _parse_timecode_seconds(duration_range_match.group(2))
        if start_val is not None and end_val is not None and end_val > start_val:
            duration = end_val - start_val

    if duration is None:
        duration_match = re.search(
            r"(?:transition\s*duration|transition\s*length|transition)\s*(?:is|=|:|of|for)?\s*(\d+(?:\.\d+)?)\s*(s|sec|secs|seconds)?",
            prompt_lower
        )
        if not duration_match:
            duration_match = re.search(
                r"(?:duration|length)\s*(?:is|=|:|of|for)?\s*(\d+(?:\.\d+)?)\s*(s|sec|secs|seconds)?",
                prompt_lower
            )
        if duration_match:
            parsed_duration = _parse_timecode_seconds(duration_match.group(1))
            if parsed_duration is not None and parsed_duration > 0:
                duration = parsed_duration

    return {"name": name, "duration": duration}


def apply_insert_with_transitions(
    base_video: Path,
    stock_item: Dict[str, Any],
    transition: Dict[str, Any],
    output_dir: Path,
    ffmpeg_tool
) -> Dict[str, Any]:
    from tools.ffmpeg_tool import _probe_media as _probe_media_ffmpeg

    stock_path = stock_item.get("path") or stock_item.get("file") or stock_item.get("source")
    if not stock_path:
        return {"success": False, "error": "Missing stock path for transition insert"}

    insert_at = _parse_timecode_seconds(stock_item.get("start_time") or stock_item.get("start"))
    if insert_at is None:
        return {"success": False, "error": "Insert transition requires start_time"}

    end_time = _parse_timecode_seconds(stock_item.get("end_time") or stock_item.get("end"))
    duration = _parse_timecode_seconds(stock_item.get("duration"))
    source_start = _parse_timecode_seconds(stock_item.get("source_start")) or 0.0
    source_end = _parse_timecode_seconds(stock_item.get("source_end"))

    base_info = _probe_media_ffmpeg(str(base_video))
    base_duration = base_info.get("duration", 0.0)
    if base_duration <= 0:
        return {"success": False, "error": "Could not determine base video duration for transitions"}

    insert_at = max(0.0, min(insert_at, base_duration))
    post_duration = max(0.0, base_duration - insert_at)

    stock_is_image = _is_image_path(str(stock_path))
    stock_info = _probe_media_ffmpeg(str(stock_path))

    stock_duration = None
    if duration is not None:
        stock_duration = max(0.0, duration)
    elif source_end is not None:
        stock_duration = max(0.0, source_end - source_start)
    elif end_time is not None:
        stock_duration = max(0.0, end_time - insert_at)
    elif not stock_is_image:
        stock_duration = max(0.0, stock_info.get("duration", 0.0) - source_start)

    if stock_duration is None or stock_duration <= 0:
        if stock_is_image:
            return {"success": False, "error": "Insert image requires duration or end_time"}
        return {"success": False, "error": "Could not determine stock duration for transitions"}

    clips: List[Dict[str, Any]] = []
    if insert_at > 0:
        clips.append({
            "path": str(base_video),
            "start_time": 0.0,
            "duration": insert_at
        })
    clips.append({
        "path": str(stock_path),
        "start_time": source_start,
        "duration": stock_duration
    })
    if post_duration > 0:
        clips.append({
            "path": str(base_video),
            "start_time": insert_at,
            "duration": post_duration
        })

    if len(clips) < 2:
        return {"success": False, "error": "Not enough clips to apply transitions"}

    transition_name = (transition or {}).get("name") or "Cross Dissolve"
    transition_duration = (transition or {}).get("duration")

    transitions_payload: Any = transition_name
    if transition_duration is not None:
        transitions_payload = [{"name": transition_name, "duration": transition_duration}]

    output_path = output_dir / f"{base_video.stem}_stock_transition.mp4"
    return ffmpeg_tool.apply_transitions(
        clips=clips,
        output_path=str(output_path),
        transitions=transitions_payload,
        transition_duration=transition_duration
    )


def should_apply_captions(prompt: str) -> bool:
    """Heuristic: detect if user asked for captions/subtitles."""
    prompt_lower = (prompt or "").lower()
    return any(kw in prompt_lower for kw in [
        "caption", "captions", "subtitle", "subtitles",
        "transcribe", "transcription", "karaoke", "word highlight"
    ])


def _first_keyword_index(text: str, keywords: list) -> Optional[int]:
    if not text or not keywords:
        return None
    lower = text.lower()
    indices = []
    for kw in keywords:
        if not kw:
            continue
        kw_lower = str(kw).lower()
        if " " in kw_lower:
            idx = lower.find(kw_lower)
            if idx != -1:
                indices.append(idx)
        else:
            match = re.search(rf"(?<!\w){re.escape(kw_lower)}(?!\w)", lower)
            if match:
                indices.append(match.start())
    return min(indices) if indices else None


def _determine_tool_order(
    prompt: str,
    has_stock: bool,
    has_cut: bool,
    stock_keywords: list,
    cut_keywords: list
) -> list:
    ops = []
    if has_stock:
        ops.append("stock")
    if has_cut:
        ops.append("cut")
    if not ops:
        return []

    prompt_text = prompt or ""
    indices = {}
    if has_stock:
        indices["stock"] = _first_keyword_index(prompt_text, stock_keywords)
    if has_cut:
        indices["cut"] = _first_keyword_index(prompt_text, cut_keywords)

    default_order = {"stock": 0, "cut": 1}

    def sort_key(op: str):
        idx = indices.get(op)
        if idx is None:
            return (1, default_order.get(op, 99))
        return (0, idx)

    return sorted(ops, key=sort_key)


def _count_steps(
    tool_order: list,
    use_captions: bool,
    apply_cut_transitions: bool = False,
    apply_rotation: bool = False
) -> int:
    steps = 1 if apply_rotation else 0
    for op in tool_order:
        if op == "stock":
            steps += 1
        elif op == "cut":
            steps += 2  # extract audio + cut
            if apply_cut_transitions:
                steps += 1
    if use_captions:
        steps += 4  # extract audio + transcribe + captions + render
    return max(steps, 1)


def load_stock_items(stock_arg: Optional[str]) -> Optional[list]:
    """Load stock items from JSON string or file path."""
    if not stock_arg:
        return None
    raw = stock_arg.strip()
    if not raw:
        return None

    try:
        possible_path = Path(raw.strip('"').strip("'"))
        if possible_path.exists():
            raw = possible_path.read_text(encoding="utf-8")
    except Exception:
        pass

    try:
        data = json.loads(raw)
    except Exception as e:
        print(f"{Fore.RED}Error: Invalid stock JSON ({e}){Style.RESET_ALL}")
        sys.exit(1)

    if isinstance(data, dict) and "stock_items" in data:
        data = data["stock_items"]

    if not isinstance(data, list):
        print(f"{Fore.RED}Error: Stock items must be a JSON list{Style.RESET_ALL}")
        sys.exit(1)

    return data


def run_caption_pipeline(
    video_path: Path, 
    prompt: str, 
    output_dir: Optional[str] = None,
    stock_items: Optional[list] = None
) -> Dict[str, Any]:
    """Run the full captioning pipeline."""
    from tools.ffmpeg_tool import FFmpegTool
    from tools.rotate_tool import RotateTool
    from tools.whisperx_tool import WhisperXTool
    from tools.captions_tool import CaptionsTool
    from tools.silence_cutter_tool import (
        SilenceCutterTool,
        should_apply_silence_cut,
        parse_silence_settings_from_prompt,
        parse_manual_cut_segments_from_prompt,
        SILENCE_KEYWORDS,
        MANUAL_CUT_VERBS
    )
    from tools.stock_footage_tool import (
        StockFootageTool,
        parse_stock_items_from_prompt,
        STOCK_KEYWORDS
    )
    from core.config_loader import ConfigLoader
    
    workspace = Path(output_dir) if output_dir else PROJECT_ROOT / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    
    def _clean_video_stem(path: Path) -> str:
        stem = path.stem
        if "__" in stem:
            stem = stem.rsplit("__", 1)[-1]
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")
        return stem or "editbot_video"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    video_name = f"{_clean_video_stem(video_path)}_{timestamp}"
    
    print(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    print(f"{Fore.GREEN}Processing: {video_path.name}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    
    ffmpeg = FFmpegTool()
    rotate_tool = RotateTool()
    config_loader = ConfigLoader()
    use_captions = should_apply_captions(prompt)
    transition_request = parse_transition_from_prompt(prompt, config_loader)
    rotation_requested = rotation_mentioned(prompt)
    rotation_cw_deg = parse_rotation_from_prompt(prompt)

    whisper_tool = None
    captions = None
    style_config = None
    highlight_options = None

    if use_captions:
        # Use int8_float16 quantization for medium model - fits well in 4GB VRAM on GPU
        whisper_tool = WhisperXTool(model_size="large-v3", device="cuda", compute_type="int8_float16")
        captions = CaptionsTool()
        style_config = parse_style_from_prompt(prompt, config_loader)
        highlight_options = parse_highlight_options_from_prompt(prompt, config_loader)
    
    silence_config = config_loader.get_config("silence_cutter") or {}
    silence_defaults = silence_config.get("defaults", {})
    silence_settings = parse_silence_settings_from_prompt(prompt, silence_defaults)
    manual_cut_segments = parse_manual_cut_segments_from_prompt(prompt)
    use_silence_cut = should_apply_silence_cut(prompt) or bool(manual_cut_segments)
    
    if use_captions and style_config:
        print(f"\n{Fore.GRAY}Detected style:{Style.RESET_ALL}")
        print(f"  Font: {style_config.font} ({style_config.font_size}px)")
        print(f"  Position: {style_config.position}")
        print(f"  Text color: {style_config.primary_color}")
        if highlight_options and highlight_options.get("enabled"):
            print(f"  Highlight: {highlight_options.get('highlight_type')}")
            print(f"  Highlight color: {highlight_options.get('highlight_color')}")
            print(f"  Current word bold: {highlight_options.get('current_word_bold')}")
            print(f"  Word box: {highlight_options.get('current_word_box')}")
    
    results = {
        "success": False,
        "video_path": str(video_path),
        "prompt": prompt,
        "outputs": {},
        "errors": []
    }

    if stock_items is None:
        parsed_items, parse_errors = parse_stock_items_from_prompt(prompt)
        if parse_errors:
            for err in parse_errors:
                print(f"{Fore.RED}Error: {err}{Style.RESET_ALL}")
            results["errors"].extend(parse_errors)
            return results
        if parsed_items:
            stock_items = parsed_items

    input_is_image = _is_image_path(str(video_path))
    if input_is_image:
        disallowed = []
        if bool(stock_items):
            disallowed.append("stock footage")
        if use_silence_cut:
            disallowed.append("silence cutting")
        if use_captions:
            disallowed.append("captions")
        if transition_request:
            disallowed.append("transitions")
        if disallowed:
            results["errors"].append(
                f"Image input currently supports rotate-only workflow. Unsupported with image: {', '.join(disallowed)}."
            )
            return results
        if not rotation_requested:
            results["errors"].append("Image input requires a rotate request (for example: 'rotate right 2 times').")
            return results
    elif rotation_requested and rotation_cw_deg is None:
        results["errors"].append(
            "Could not parse rotation amount. Use degrees or left/right turns, e.g. 'rotate 45 degrees' or 'rotate left 2 times'."
        )
        return results

    try:
        cut_keywords = list(dict.fromkeys(list(MANUAL_CUT_VERBS) + list(SILENCE_KEYWORDS)))
        tool_order = _determine_tool_order(
            prompt=prompt,
            has_stock=bool(stock_items),
            has_cut=use_silence_cut,
            stock_keywords=STOCK_KEYWORDS,
            cut_keywords=cut_keywords
        )
        apply_cut_transitions = bool(transition_request) and ("cut" in tool_order)
        apply_rotation = rotation_cw_deg is not None
        step_total = _count_steps(tool_order, use_captions, apply_cut_transitions, apply_rotation)
        step_index = 1

        def print_step(label: str) -> None:
            nonlocal step_index
            print(f"\n{Fore.YELLOW}[{step_index}/{step_total}] {label}{Style.RESET_ALL}")
            step_index += 1

        current_video = Path(video_path)
        audio_path = None

        if rotation_cw_deg is not None:
            print_step("Rotating media...")
            rotated_ext = current_video.suffix if current_video.suffix else ".mp4"
            rotated_output = workspace / f"{video_name}_rotated{rotated_ext}"
            rotate_result = rotate_tool.rotate_media(
                input_path=str(current_video),
                output_path=str(rotated_output),
                rotation_cw_deg=rotation_cw_deg
            )
            if not rotate_result.get("success"):
                results["errors"].append(f"Rotate failed: {rotate_result.get('error')}")
                print(f"{Fore.RED}Failed to rotate media{Style.RESET_ALL}")
                return results
            current_video = Path(rotate_result.get("output_path", rotated_output))
            results["outputs"]["rotated_media"] = str(current_video)
            audio_path = None

            has_other_ops = bool(tool_order) or use_captions
            if not has_other_ops:
                results["outputs"]["media"] = str(current_video)
                if not input_is_image:
                    results["outputs"]["video"] = str(current_video)
                results["success"] = True
                return results

        for op in tool_order:
            if op == "stock":
                print_step("Applying stock footage...")
                if transition_request and stock_items:
                    insert_items = [
                        item for item in stock_items
                        if _normalize_stock_mode(item.get("mode")) == "insert"
                    ]
                    if len(stock_items) == 1 and len(insert_items) == 1:
                        transition_result = apply_insert_with_transitions(
                            base_video=current_video,
                            stock_item=insert_items[0],
                            transition=transition_request,
                            output_dir=workspace,
                            ffmpeg_tool=ffmpeg
                        )
                        if not transition_result.get("success"):
                            results["errors"].append(
                                f"Stock footage failed: {transition_result.get('error')}"
                            )
                            print(f"{Fore.RED}Failed to apply stock footage with transitions{Style.RESET_ALL}")
                            return results
                        current_video = Path(transition_result.get("output_path", workspace / f"{video_name}_stock_transition.mp4"))
                        results["outputs"]["stock_video"] = str(current_video)
                        audio_path = None
                        continue
                    else:
                        results["errors"].append(
                            "Transitions with stock footage are supported only for a single insert item currently."
                        )
                        print(f"{Fore.RED}Stock footage transitions not supported for this setup{Style.RESET_ALL}")
                        return results

                stock_tool = StockFootageTool()
                stock_output = workspace / f"{video_name}_stock.mp4"
                stock_result = stock_tool.apply_stock_footage(
                    video_path=str(current_video),
                    stock_items=stock_items,
                    output_path=str(stock_output)
                )
                if not stock_result.get("success"):
                    results["errors"].append(f"Stock footage failed: {stock_result.get('error')}")
                    print(f"{Fore.RED}Failed to apply stock footage{Style.RESET_ALL}")
                    return results
                current_video = Path(stock_result.get("output_path", stock_output))
                results["outputs"]["stock_video"] = str(current_video)
                audio_path = None
                continue

            if op == "cut":
                cut_source_video = Path(current_video)
                print_step("Extracting audio for cutting...")
                audio_path = workspace / f"{video_name}_audio_cut_src.wav"
                audio_result = ffmpeg.extract_audio(str(current_video), str(audio_path))
                if not audio_result.get("success"):
                    results["errors"].append(f"Audio extraction failed: {audio_result.get('error')}")
                    print(f"{Fore.RED}Failed to extract audio{Style.RESET_ALL}")
                    return results
                print(f"{Fore.GREEN}Audio extracted: {audio_path.name}{Style.RESET_ALL}")
                results["outputs"]["audio"] = str(audio_path)

                print_step("Cutting silences...")
                silence_tool = SilenceCutterTool()
                cut_video_path = workspace / f"{video_name}_cut.mp4"
                cut_audio_path = workspace / f"{video_name}_cut.wav"
                cut_list_path = workspace / f"{video_name}_cut_list.json"

                cut_result = silence_tool.cut_silence(
                    audio_path=str(audio_path),
                    video_path=str(current_video),
                    output_path=str(cut_video_path),
                    output_audio_path=str(cut_audio_path),
                    cut_list_path=str(cut_list_path),
                    manual_cut_segments=manual_cut_segments if manual_cut_segments else None,
                    threshold_db=silence_settings.get("threshold_db"),
                    min_silence_duration=silence_settings.get("min_silence_duration"),
                    padding=silence_settings.get("padding"),
                    chunk_ms=silence_settings.get("chunk_ms"),
                    filler_detection=silence_settings.get("filler_detection"),
                    filler_model_size=silence_settings.get("filler_model_size"),
                    filler_language=silence_settings.get("filler_language"),
                    filler_confidence=silence_settings.get("filler_confidence"),
                    filler_aggressive=silence_settings.get("filler_aggressive"),
                    filler_engine=silence_settings.get("filler_engine"),
                    filler_words=silence_config.get("filler_words"),
                    filler_phrases=silence_config.get("filler_phrases")
                )

                if not cut_result.get("success"):
                    results["errors"].append(f"Silence cut failed: {cut_result.get('error')}")
                    print(f"{Fore.RED}Failed to cut silences{Style.RESET_ALL}")
                    return results

                results["outputs"]["cut_list"] = cut_result.get("cut_list_path")
                if cut_result.get("output_video_path"):
                    results["outputs"]["cut_video"] = cut_result.get("output_video_path")
                    current_video = Path(cut_result["output_video_path"])
                if cut_result.get("output_audio_path"):
                    results["outputs"]["cut_audio"] = cut_result.get("output_audio_path")
                    audio_path = Path(cut_result["output_audio_path"])

                keep_segments = cut_result.get("keep_segments") or []
                if transition_request and isinstance(keep_segments, list) and len(keep_segments) >= 2:
                    print_step("Applying transitions...")
                    cut_transition_path = workspace / f"{video_name}_cut_transition.mp4"
                    transition_name = transition_request.get("name") or "Cross Dissolve"
                    transition_duration = transition_request.get("duration")
                    transition_item: Dict[str, Any] = {"name": transition_name}
                    if transition_duration is not None:
                        transition_item["duration"] = transition_duration

                    transition_result = ffmpeg.apply_transitions(
                        source_path=str(cut_source_video),
                        segments=keep_segments,
                        output_path=str(cut_transition_path),
                        transitions=[transition_item],
                        transition_duration=transition_duration
                    )
                    if not transition_result.get("success"):
                        results["errors"].append(
                            f"Transition application failed: {transition_result.get('error')}"
                        )
                        print(f"{Fore.RED}Failed to apply transitions{Style.RESET_ALL}")
                        return results

                    current_video = Path(transition_result.get("output_path", cut_transition_path))
                    results["outputs"]["cut_video"] = str(current_video)
                    audio_path = None

                print(f"{Fore.GREEN}Silences removed{Style.RESET_ALL}")

        video_path = current_video

        if not use_captions:
            results["outputs"]["video"] = str(video_path)
            results["success"] = True
            return results

        print_step("Extracting audio for captions...")
        caption_audio_path = workspace / f"{video_name}_audio_caption.wav"
        audio_result = ffmpeg.extract_audio(str(video_path), str(caption_audio_path))
        if not audio_result.get("success"):
            results["errors"].append(f"Audio extraction failed: {audio_result.get('error')}")
            print(f"{Fore.RED}Failed to extract audio{Style.RESET_ALL}")
            return results
        print(f"{Fore.GREEN}Audio extracted: {caption_audio_path.name}{Style.RESET_ALL}")
        results["outputs"]["audio"] = str(caption_audio_path)

        # Next step: Transcribe
        print(f"\n{Fore.YELLOW}[{step_index}/{step_total}] 🎤 Transcribing speech...{Style.RESET_ALL}")
        print(f"{Fore.GRAY}  (First run downloads ~150MB model){Style.RESET_ALL}")
        
        asr_audio_path = caption_audio_path
        speed = CAPTION_AUDIO_SPEED
        if speed and abs(speed - 1.0) > 1e-3:
            slowed_audio_path = caption_audio_path.with_name(
                f"{caption_audio_path.stem}_slow{int(speed * 100)}.wav"
            )
            print(f"{Fore.GRAY}  Slowing audio to {speed}x for ASR...{Style.RESET_ALL}")
            slow_audio(Path(caption_audio_path), slowed_audio_path, speed)
            asr_audio_path = slowed_audio_path

        transcription = whisper_tool.transcribe_and_align(str(asr_audio_path))
        
        if not transcription.get("success"):
            results["errors"].append(f"Transcription failed: {transcription.get('error')}")
            print(f"{Fore.RED}✗ Failed to transcribe{Style.RESET_ALL}")
            return results
        
        if speed and abs(speed - 1.0) > 1e-3:
            transcription = scale_transcription_timestamps(transcription, speed)

        words = transcription.get("words", [])
        language = transcription.get("language", "unknown")
        print(f"{Fore.GREEN}✓ Transcribed {len(words)} words (Language: {language}){Style.RESET_ALL}")
        step_index += 1
        
        transcript_path = workspace / f"{video_name}_transcript.json"
        with open(transcript_path, 'w', encoding='utf-8') as f:
            json.dump(transcription, f, indent=2, ensure_ascii=False)
        results["outputs"]["transcript"] = str(transcript_path)
        
        # Step: Generate captions
        print(f"\n{Fore.YELLOW}[{step_index}/{step_total}] 📝 Generating captions...{Style.RESET_ALL}")
        
        ass_path = workspace / f"{video_name}_captions.ass"
        
        video_info = ffmpeg.get_video_info(str(video_path))
        width = video_info.get("width", 1920)
        height = video_info.get("height", 1080)
        
        caption_result = captions.generate_ass_file(
            words=words,
            output_path=str(ass_path),
            style=style_config,
            video_width=width,
            video_height=height,
            highlight_options=highlight_options,
            detected_language=language
        )
        
        if not caption_result.get("success"):
            results["errors"].append(f"Caption generation failed: {caption_result.get('error')}")
            print(f"{Fore.RED}✗ Failed to generate captions{Style.RESET_ALL}")
            return results
        
        print(f"{Fore.GREEN}✓ Generated {caption_result.get('total_lines', 0)} caption lines{Style.RESET_ALL}")
        step_index += 1
        results["outputs"]["subtitles"] = str(ass_path)
        
        # Step: Render video with subtitles
        print(f"\n{Fore.YELLOW}[{step_index}/{step_total}] 🎬 Rendering final video...{Style.RESET_ALL}")
        
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


def process_video(video_path: Path, prompt: str, output_dir: str = None, stock_items: Optional[list] = None) -> Dict[str, Any]:
    """Process video with the given prompt"""
    
    result = run_caption_pipeline(video_path, prompt, output_dir, stock_items)
    
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
    parser.add_argument("--stock", type=str, default=None, help="JSON list or path to JSON file for stock items")
    parser.add_argument("-i", "--interactive", action="store_true", help="Interactive mode")
    
    args = parser.parse_args()
    
    if args.interactive or (not args.video and not args.prompt):
        video_path, prompt = interactive_mode()
    else:
        if not args.video:
            parser.error("--video is required (or use --interactive)")
        if not args.prompt:
            parser.error("--prompt is required (or use --interactive)")
        prompt = args.prompt
        video_path = validate_media(args.video, prompt)
    
    stock_items = load_stock_items(args.stock)
    result = process_video(video_path, prompt, args.output, stock_items)
    
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
