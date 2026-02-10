from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
import inspect

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.schema import CaptionStyle
from tools.ffmpeg_tool import FFmpegTool
from tools.captions_tool import CaptionsTool
from tools.whisperx_tool import WhisperXTool
DEFAULT_VIDEO = PROJECT_ROOT / "reference videos" / "arabic.mp4"
WORKSPACE_DIR = PROJECT_ROOT / "workspace"


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


def scale_word_timestamps(words: list[dict], factor: float) -> list[dict]:
    scaled = []
    for word in words:
        scaled.append(
            {
                **word,
                "start": float(word.get("start", 0.0)) * factor,
                "end": float(word.get("end", 0.0)) * factor,
            }
        )
    return scaled


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test captions with 0.85x audio slowdown using existing whisper/captions tools."
    )
    parser.add_argument("--video_path", default=str(DEFAULT_VIDEO))
    parser.add_argument("--output_dir", default=str(WORKSPACE_DIR))

    # Speed <1.0 slows down audio, >1.0 speeds up. 0.85 means 15% slower.
    parser.add_argument("--speed", type=float, default=0.85)

    parser.add_argument("--language", default=None)
    parser.add_argument("--denoise", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--dereverb", action=argparse.BooleanOptionalAction, default=False)
    args = parser.parse_args()

    video_path = Path(args.video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    speed = float(args.speed)
    speed_tag = f"{int(speed * 100)}"

    audio_path = output_dir / f"{video_path.stem}_audio.wav"
    slowed_audio_path = output_dir / f"{video_path.stem}_audio_slow{speed_tag}.wav"
    ass_path = output_dir / f"{video_path.stem}_captions_slow{speed_tag}.ass"
    output_video = output_dir / f"{video_path.stem}_captions_slow{speed_tag}.mp4"

    ffmpeg_tool = FFmpegTool()
    captions_tool = CaptionsTool()
    whisper_tool = WhisperXTool(model_size="large-v3", device="cuda", compute_type="int8_float16")

    print(f"Extracting audio from: {video_path}")
    extract_result = ffmpeg_tool.extract_audio(
        video_path=str(video_path),
        output_path=str(audio_path),
        sample_rate=16000,
        audio_format="wav",
    )
    if not extract_result.get("success"):
        raise RuntimeError(extract_result.get("error", "Audio extraction failed"))

    print(f"Slowing audio to {speed}x: {slowed_audio_path}")
    slow_audio(audio_path, slowed_audio_path, speed)

    print("Transcribing slowed audio...")
    transcribe_kwargs = {"language": args.language} if args.language else {}
    sig = inspect.signature(whisper_tool.transcribe_and_align)
    if "preprocess" in sig.parameters:
        transcribe_kwargs["preprocess"] = True
    if "denoise" in sig.parameters:
        transcribe_kwargs["denoise"] = args.denoise
    if "dereverb" in sig.parameters:
        transcribe_kwargs["dereverb"] = args.dereverb

    transcription = whisper_tool.transcribe_and_align(
        str(slowed_audio_path),
        **transcribe_kwargs,
    )
    if not transcription.get("success"):
        raise RuntimeError(transcription.get("error", "Transcription failed"))

    words = transcription.get("words", [])
    if not words:
        raise RuntimeError("No words returned from transcription")

    # Map timestamps back to original video timebase.
    words_scaled = scale_word_timestamps(words, speed)

    info = ffmpeg_tool.get_video_info(str(video_path))
    if info.get("success"):
        width = info.get("width", 1920)
        height = info.get("height", 1080)
    else:
        width = 1920
        height = 1080

    style = CaptionStyle()

    print(f"Generating captions: {ass_path}")
    ass_result = captions_tool.generate_ass_file(
        words=words_scaled,
        output_path=str(ass_path),
        style=style,
        video_width=width,
        video_height=height,
    )
    if not ass_result.get("success"):
        raise RuntimeError(ass_result.get("error", "ASS generation failed"))

    print(f"Rendering subtitles to: {output_video}")
    render_result = ffmpeg_tool.render_subtitles(
        video_path=str(video_path),
        subtitle_path=str(ass_path),
        output_path=str(output_video),
    )
    if not render_result.get("success"):
        raise RuntimeError(render_result.get("error", "Subtitle render failed"))

    print("\nDone")
    print(f"Output video: {output_video}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
