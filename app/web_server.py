"""
EditBot Studio - FastAPI server for the web UI.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load environment variables if present
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

from core.config_loader import get_config_loader
from core.clarifier import detect_time_conflicts, generate_clarification
from core.assistant import (
    classify_intent,
    generate_response,
    generate_response_stream,
    remember_chat_message,
    remember_operation,
)
from app.main import (
    process_video,
    parse_style_from_prompt,
    parse_highlight_options_from_prompt,
    CAPTION_AUDIO_SPEED,
    slow_audio,
    scale_transcription_timestamps
)

WEB_DIR = PROJECT_ROOT / "web"
STATIC_DIR = WEB_DIR / "static"
UPLOAD_DIR = PROJECT_ROOT / "workspace" / "uploads"
UPLOAD_INDEX = UPLOAD_DIR / "index.json"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

UPLOAD_LOCK = threading.Lock()
MEMORY_LOCK = threading.Lock()
SESSION_MEMORY: Dict[str, Dict[str, Any]] = {}
SESSION_MEMORY_PATH = PROJECT_ROOT / "workspace" / "memory" / "session_memory.json"
MAX_HISTORY = 40
MAX_OPERATIONS = 30


class ProcessRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    video_id: Optional[str] = None
    video_path: Optional[str] = None
    output_dir: Optional[str] = None
    stock_items: Optional[list] = None
    session_id: Optional[str] = None
    cleanup_uploads: bool = False


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    video_id: Optional[str] = None
    video_path: Optional[str] = None
    output_dir: Optional[str] = None
    stock_items: Optional[list] = None
    session_id: Optional[str] = None


app = FastAPI(title="EditBot Studio", version="1.0.0")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def disable_cache_for_ui(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path or ""
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def _load_upload_index() -> Dict[str, Any]:
    if not UPLOAD_INDEX.exists():
        return {}
    try:
        return json.loads(UPLOAD_INDEX.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_upload_index(index: Dict[str, Any]) -> None:
    UPLOAD_INDEX.write_text(json.dumps(index, indent=2), encoding="utf-8")


def _prune_missing_uploads(index: Dict[str, Any]) -> Dict[str, Any]:
    if not index:
        return {}
    pruned = {}
    for file_id, record in index.items():
        path = record.get("path")
        if path and Path(path).exists():
            pruned[file_id] = record
    if len(pruned) != len(index):
        _save_upload_index(pruned)
    return pruned


def _filter_index_by_session(index: Dict[str, Any], session_id: Optional[str]) -> Dict[str, Any]:
    if not session_id:
        return index
    return {
        file_id: record
        for file_id, record in index.items()
        if record.get("session_id") == session_id
    }


def _cleanup_session_uploads(index: Dict[str, Any], session_id: Optional[str]) -> Dict[str, Any]:
    if not index:
        return {"removed": [], "remaining": 0}

    upload_root = UPLOAD_DIR.resolve()
    removed: List[Dict[str, Any]] = []
    kept: Dict[str, Any] = {}

    for file_id, record in index.items():
        if session_id and record.get("session_id") != session_id:
            kept[file_id] = record
            continue

        path_value = record.get("path")
        if path_value:
            try:
                path = Path(path_value).resolve()
                if upload_root in path.parents or path == upload_root:
                    if path.exists():
                        path.unlink()
            except Exception:
                # Keep going; cleanup is best-effort
                pass

        removed.append(record)

    _save_upload_index(kept)
    return {"removed": removed, "remaining": len(kept)}


def _clear_active_video_refs(file_id: str, removed_path: Optional[str] = None) -> None:
    with MEMORY_LOCK:
        for session in SESSION_MEMORY.values():
            active_id = session.get("active_video_id")
            active_path = session.get("active_video_path")
            if active_id == file_id or (removed_path and active_path == removed_path):
                session["active_video_id"] = None
                session["active_video_path"] = None
                session["active_video_name"] = None


def _sanitize_filename(filename: str) -> str:
    name = Path(filename).name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name or "upload"

_STOCK_INTENT_KEYWORDS = (
    "stock",
    "stock footage",
    "stock video",
    "stock clip",
    "b-roll",
    "b roll",
    "broll",
    "cutaway"
)


def _inject_uploaded_paths(prompt: str, index: Dict[str, Any], exclude_ids: Optional[set] = None) -> str:
    if not prompt:
        return prompt

    updated = prompt
    candidates = [
        record for record in index.values()
        if not exclude_ids or record.get("id") not in exclude_ids
    ]

    def apply_replacement(value: Optional[str], path: str) -> None:
        nonlocal updated
        if not value:
            return
        pattern = re.compile(
            rf"(?<![\\/:\w]){re.escape(value)}(?!\w)",
            re.IGNORECASE
        )
        updated = pattern.sub(lambda _: path, updated)

    for record in sorted(candidates, key=lambda r: len(r.get("name", "")), reverse=True):
        path = record.get("path")
        if not path:
            continue
        apply_replacement(record.get("name"), path)
        apply_replacement(record.get("stored_name"), path)

    return updated


def _maybe_append_stock_path(prompt: str, index: Dict[str, Any], exclude_ids: Optional[set] = None) -> str:
    if not prompt:
        return prompt

    lower = prompt.lower()
    if not any(keyword in lower for keyword in _STOCK_INTENT_KEYWORDS):
        return prompt

    candidates = [
        record for record in index.values()
        if not exclude_ids or record.get("id") not in exclude_ids
    ]

    for record in candidates:
        name = (record.get("name") or "").lower()
        stored = (record.get("stored_name") or "").lower()
        path = (record.get("path") or "").lower()
        if (name and name in lower) or (stored and stored in lower) or (path and path in lower):
            return prompt

    if len(candidates) == 1 and candidates[0].get("path"):
        return f"{prompt} {candidates[0]['path']}"

    return prompt


def _get_session_memory(session_id: Optional[str]) -> Dict[str, Any]:
    key = session_id or "default"
    with MEMORY_LOCK:
        if key not in SESSION_MEMORY:
            # Try to load from disk
            persisted = _load_persisted_sessions()
            if key in persisted:
                SESSION_MEMORY[key] = persisted[key]
            else:
                SESSION_MEMORY[key] = {
                    "history": [],
                    "operations": [],
                    "active_video_id": None,
                    "active_video_path": None,
                    "active_video_name": None,
                    "session_id": key
                }
        else:
            SESSION_MEMORY[key].setdefault("session_id", key)
        return SESSION_MEMORY[key]


def _load_persisted_sessions() -> Dict[str, Any]:
    """Load session memory from disk."""
    try:
        if SESSION_MEMORY_PATH.exists():
            data = json.loads(SESSION_MEMORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _persist_session_memory(session_id: Optional[str] = None) -> None:
    """Save session memory to disk for persistence across restarts."""
    try:
        SESSION_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with MEMORY_LOCK:
            data = dict(SESSION_MEMORY)
        SESSION_MEMORY_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8"
        )
    except Exception:
        pass


def _append_history(session: Dict[str, Any], role: str, content: str) -> None:
    if not content:
        return
    session["history"].append({
        "role": role,
        "content": content
    })
    if len(session["history"]) > MAX_HISTORY:
        session["history"] = session["history"][-MAX_HISTORY:]
    try:
        remember_chat_message(session.get("session_id"), role, content)
    except Exception:
        pass
    # Persist to disk
    _persist_session_memory(session.get("session_id"))


def _log_operation(session: Dict[str, Any], entry: Dict[str, Any]) -> None:
    session["operations"].append(entry)
    if len(session["operations"]) > MAX_OPERATIONS:
        session["operations"] = session["operations"][-MAX_OPERATIONS:]
    try:
        remember_operation(session.get("session_id"), entry)
    except Exception:
        pass
    # Persist to disk
    _persist_session_memory(session.get("session_id"))


def _build_memory_summary(session: Dict[str, Any]) -> str:
    """Build a rich memory summary for the LLM to understand session state."""
    lines: List[str] = []
    active_name = session.get("active_video_name")
    active_path = session.get("active_video_path")
    if active_name or active_path:
        lines.append(f"Active video: {active_name or active_path}")

    ops = session.get("operations", [])
    if not ops:
        lines.append("No operations have been performed yet in this session.")
        return "\n".join(lines)

    lines.append(f"Total operations performed: {len(ops)}")
    lines.append("Operations log (most recent last):")
    for idx, op in enumerate(ops, 1):
        timestamp = op.get("timestamp", "unknown time")
        action = op.get("action", "operation")
        summary = op.get("summary") or op.get("prompt") or "completed"
        outputs = op.get("outputs") or {}
        output_details = []
        for key, value in sorted(outputs.items()):
            output_details.append(f"{key}: {value}")
        output_str = "; ".join(output_details) if output_details else "no output files"
        prompt_used = op.get("prompt", "")
        lines.append(f"  {idx}. [{timestamp}] {action}: {summary}")
        if prompt_used:
            lines.append(f"     User prompt: \"{prompt_used}\"")
        lines.append(f"     Output files: {output_str}")

    return "\n".join(lines)

def _supported_video_extensions() -> List[str]:
    loader = get_config_loader()
    formats = loader.get_config("supported_formats")
    return formats.get("video_extensions", {}).get("input", [])


def _supported_image_extensions() -> List[str]:
    loader = get_config_loader()
    formats = loader.get_config("supported_formats")
    return formats.get("image_extensions", {}).get("input", [])


def _clean_video_stem(path: Path) -> str:
    stem = path.stem
    if "__" in stem:
        stem = stem.rsplit("__", 1)[-1]
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")
    return stem or "editbot_video"


def _build_output_basename(video_path: Path) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{_clean_video_stem(video_path)}_{timestamp}"


def _format_video_info(info: Dict[str, Any]) -> str:
    duration = info.get("duration", 0)
    width = info.get("width", 0)
    height = info.get("height", 0)
    fps = info.get("fps", 0)
    has_audio = info.get("has_audio", False)
    sample_rate = info.get("audio_sample_rate")
    channel_layout = info.get("audio_channel_layout")

    lines = [
        f"Duration: {duration:.2f}s",
        f"Resolution: {width}x{height}",
        f"FPS: {fps:.2f}" if fps else "FPS: unknown",
        f"Audio: {'yes' if has_audio else 'no'}",
    ]
    if has_audio and sample_rate:
        lines.append(f"Audio sample rate: {sample_rate} Hz")
    if has_audio and channel_layout:
        lines.append(f"Audio channels: {channel_layout}")
    return "\n".join(lines)


def _parse_caption_format(prompt: str) -> str:
    text = (prompt or "").lower()
    if "srt" in text:
        return "srt"
    if "vtt" in text or "webvtt" in text:
        return "vtt"
    return "ass"


def _format_timestamp_srt(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    ms = total_ms % 1000
    total_seconds = total_ms // 1000
    s = total_seconds % 60
    m = (total_seconds // 60) % 60
    h = total_seconds // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_timestamp_vtt(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    ms = total_ms % 1000
    total_seconds = total_ms // 1000
    s = total_seconds % 60
    m = (total_seconds // 60) % 60
    h = total_seconds // 3600
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _build_caption_entries(transcription: Dict[str, Any]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    segments = transcription.get("segments") or []
    if segments:
        for seg in segments:
            text = (seg.get("text") or "").strip()
            start = seg.get("start")
            end = seg.get("end")
            if not text or start is None or end is None:
                continue
            if end < start:
                start, end = end, start
            entries.append({
                "start": float(start),
                "end": float(end),
                "text": text
            })
        if entries:
            return entries

    words = transcription.get("words") or []
    cleaned = []
    for word in words:
        token = str(word.get("word", "")).strip()
        if not token:
            continue
        try:
            start = float(word.get("start", 0.0))
        except Exception:
            start = 0.0
        try:
            end = float(word.get("end", start))
        except Exception:
            end = start
        if end < start:
            end = start
        cleaned.append({"word": token, "start": start, "end": end})

    cleaned.sort(key=lambda w: (w["start"], w["end"]))

    lines = []
    current = []
    line_start = None
    for word in cleaned:
        if not current:
            line_start = word["start"]
        current.append(word)
        if len(current) >= 6 or (word["end"] - (line_start or word["start"])) >= 4.0:
            lines.append(current)
            current = []
            line_start = None
    if current:
        lines.append(current)

    for line in lines:
        start = line[0]["start"]
        end = line[-1]["end"]
        text = " ".join(w["word"] for w in line)
        entries.append({"start": start, "end": end, "text": text})

    return entries


def _write_srt(entries: List[Dict[str, Any]], output_path: Path) -> None:
    lines: List[str] = []
    for idx, entry in enumerate(entries, start=1):
        lines.append(str(idx))
        lines.append(f"{_format_timestamp_srt(entry['start'])} --> {_format_timestamp_srt(entry['end'])}")
        lines.append(entry["text"])
        lines.append("")
    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _write_vtt(entries: List[Dict[str, Any]], output_path: Path) -> None:
    lines: List[str] = ["WEBVTT", ""]
    for entry in entries:
        lines.append(f"{_format_timestamp_vtt(entry['start'])} --> {_format_timestamp_vtt(entry['end'])}")
        lines.append(entry["text"])
        lines.append("")
    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")



def _load_tools_registry() -> Dict[str, Any]:
    path = PROJECT_ROOT / "registry" / "tools.json"
    if not path.exists():
        return {"tools": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_examples() -> List[str]:
    examples_path = PROJECT_ROOT / "prompts" / "user_examples.md"
    if not examples_path.exists():
        return []

    text = examples_path.read_text(encoding="utf-8")
    blocks: List[str] = []
    in_block = False
    current: List[str] = []

    for line in text.splitlines():
        if line.strip().startswith("```"):
            if in_block:
                example = "\n".join(current).strip()
                if example:
                    blocks.append(example)
                current = []
                in_block = False
            else:
                in_block = True
            continue
        if in_block:
            current.append(line)

    if not blocks:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("\"") and line.endswith("\""):
                blocks.append(line.strip("\""))

    return blocks


def _resolve_video(
    video_id: Optional[str],
    video_path: Optional[str],
    session_id: Optional[str]
) -> Dict[str, Any]:
    index = _prune_missing_uploads(_load_upload_index())
    index = _filter_index_by_session(index, session_id)

    record = None
    resolved_path = None
    if video_id:
        record = index.get(video_id)
        if record:
            resolved_path = record.get("path")
    elif video_path:
        resolved_path = video_path

    return {
        "path": Path(resolved_path) if resolved_path else None,
        "record": record,
        "index": index
    }


def _validate_video_path(path: Path) -> Optional[str]:
    if not path.exists():
        return "Uploaded media file is missing. Please re-upload."
    ext = path.suffix.lower()
    if ext not in _supported_video_extensions() and ext not in _supported_image_extensions():
        return "Unsupported media format"
    return None


async def _run_edit_pipeline(
    prompt: str,
    video_path: Path,
    index: Dict[str, Any],
    session_id: Optional[str],
    output_dir: Optional[str],
    stock_items: Optional[list]
) -> Dict[str, Any]:
    exclude_ids = set()
    if index:
        # Exclude active video from auto-stock injection
        exclude_ids = {record.get("id") for record in index.values() if record.get("path") == str(video_path)}

    if stock_items is None:
        prompt = _inject_uploaded_paths(prompt, index, exclude_ids)
        prompt = _maybe_append_stock_path(prompt, index, exclude_ids)

    conflicts = detect_time_conflicts(prompt)
    if conflicts:
        clarification = generate_clarification(prompt, conflicts)
        if clarification:
            return {
                "success": False,
                "needs_clarification": True,
                "clarification": clarification,
                "conflicts": conflicts
            }
        return {
            "success": False,
            "needs_clarification": True,
            "clarification": {
                "question": "Clarification needed, but the LLM is unavailable. Please rephrase or specify the order explicitly.",
                "options": []
            },
            "conflicts": conflicts
        }

    result = await asyncio.to_thread(
        process_video,
        video_path,
        prompt,
        output_dir,
        stock_items
    )
    result["prompt"] = prompt
    return result


def _run_audio_extract(video_path: Path, output_dir: Optional[str]) -> Dict[str, Any]:
    from tools.ffmpeg_tool import FFmpegTool

    workspace = Path(output_dir) if output_dir else PROJECT_ROOT / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    base = _build_output_basename(video_path)
    audio_path = workspace / f"{base}_audio.wav"

    ffmpeg = FFmpegTool()
    result = ffmpeg.extract_audio(str(video_path), str(audio_path))
    if not result.get("success"):
        return {"success": False, "errors": [result.get("error", "Audio extraction failed")]}
    return {
        "success": True,
        "outputs": {
            "audio": str(audio_path)
        }
    }


def _run_transcribe_only(video_path: Path, output_dir: Optional[str]) -> Dict[str, Any]:
    from tools.ffmpeg_tool import FFmpegTool
    from tools.whisperx_tool import WhisperXTool

    workspace = Path(output_dir) if output_dir else PROJECT_ROOT / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    base = _build_output_basename(video_path)
    audio_path = workspace / f"{base}_audio.wav"

    ffmpeg = FFmpegTool()
    audio_result = ffmpeg.extract_audio(str(video_path), str(audio_path))
    if not audio_result.get("success"):
        return {"success": False, "errors": [audio_result.get("error", "Audio extraction failed")]}

    whisper = WhisperXTool(model_size="large-v3", device="cuda", compute_type="int8_float16")
    transcription = whisper.transcribe_and_align(str(audio_path))
    if not transcription.get("success"):
        return {"success": False, "errors": [transcription.get("error", "Transcription failed")]}

    transcript_path = workspace / f"{base}_transcript.json"
    transcript_path.write_text(json.dumps(transcription, indent=2, ensure_ascii=False), encoding="utf-8")

    text_preview = transcription.get("full_text") or transcription.get("text") or ""
    if len(text_preview) > 800:
        text_preview = text_preview[:800].rstrip() + "..."

    return {
        "success": True,
        "outputs": {
            "audio": str(audio_path),
            "transcript": str(transcript_path)
        },
        "preview": text_preview
    }


def _run_captions_only(video_path: Path, prompt: str, output_dir: Optional[str]) -> Dict[str, Any]:
    from tools.ffmpeg_tool import FFmpegTool
    from tools.whisperx_tool import WhisperXTool
    from tools.captions_tool import CaptionsTool

    workspace = Path(output_dir) if output_dir else PROJECT_ROOT / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    base = _build_output_basename(video_path)

    ffmpeg = FFmpegTool()
    audio_path = workspace / f"{base}_audio.wav"
    audio_result = ffmpeg.extract_audio(str(video_path), str(audio_path))
    if not audio_result.get("success"):
        return {"success": False, "errors": [audio_result.get("error", "Audio extraction failed")]}

    asr_audio_path = audio_path
    speed = CAPTION_AUDIO_SPEED
    if speed and abs(speed - 1.0) > 1e-3:
        slowed_audio_path = audio_path.with_name(f"{audio_path.stem}_slow{int(speed * 100)}.wav")
        try:
            slow_audio(audio_path, slowed_audio_path, speed)
            asr_audio_path = slowed_audio_path
        except Exception as exc:
            return {"success": False, "errors": [f"Audio preparation failed: {exc}"]}

    whisper = WhisperXTool(model_size="large-v3", device="cuda", compute_type="int8_float16")
    transcription = whisper.transcribe_and_align(str(asr_audio_path))
    if not transcription.get("success"):
        return {"success": False, "errors": [transcription.get("error", "Transcription failed")]}

    if speed and abs(speed - 1.0) > 1e-3:
        transcription = scale_transcription_timestamps(transcription, speed)

    words = transcription.get("words", [])
    language = transcription.get("language")

    caption_format = _parse_caption_format(prompt)
    wants_transcript = "transcript" in (prompt or "").lower() or "json" in (prompt or "").lower()

    outputs: Dict[str, str] = {}
    if wants_transcript:
        transcript_path = workspace / f"{base}_transcript.json"
        transcript_path.write_text(json.dumps(transcription, indent=2, ensure_ascii=False), encoding="utf-8")
        outputs["transcript"] = str(transcript_path)

    if caption_format in ("srt", "vtt"):
        entries = _build_caption_entries(transcription)
        if not entries:
            return {"success": False, "errors": ["No transcription segments available for captions."]}
        subtitle_path = workspace / f"{base}_captions.{caption_format}"
        if caption_format == "srt":
            _write_srt(entries, subtitle_path)
        else:
            _write_vtt(entries, subtitle_path)
        outputs["subtitles"] = str(subtitle_path)
        return {
            "success": True,
            "outputs": outputs
        }

    config_loader = get_config_loader()
    style = parse_style_from_prompt(prompt, config_loader)
    highlight_options = parse_highlight_options_from_prompt(prompt, config_loader)

    video_info = ffmpeg.get_video_info(str(video_path))
    width = video_info.get("width", 1920)
    height = video_info.get("height", 1080)

    captions = CaptionsTool()
    ass_path = workspace / f"{base}_captions.ass"
    caption_result = captions.generate_ass_file(
        words=words,
        output_path=str(ass_path),
        style=style,
        video_width=width,
        video_height=height,
        highlight_options=highlight_options,
        detected_language=language
    )
    if not caption_result.get("success"):
        return {"success": False, "errors": [caption_result.get("error", "Caption generation failed")]}

    outputs["subtitles"] = str(ass_path)
    return {
        "success": True,
        "outputs": outputs
    }


@app.get("/")
def index() -> FileResponse:
    index_file = WEB_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="UI not found")
    return FileResponse(index_file)


@app.get("/position-helper")
def position_helper_page() -> FileResponse:
    helper_file = WEB_DIR / "position_helper.html"
    if not helper_file.exists():
        raise HTTPException(status_code=404, detail="Position helper UI not found")
    return FileResponse(helper_file)


@app.get("/timeline")
def timeline_page() -> FileResponse:
    timeline_file = WEB_DIR / "timeline.html"
    if not timeline_file.exists():
        raise HTTPException(status_code=404, detail="Timeline viewer UI not found")
    return FileResponse(timeline_file)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.on_event("startup")
async def startup_event():
    """Initialize memory index and warm LLM on server start."""
    import threading
    from core.assistant import _get_memory
    def _init():
        try:
            memory = _get_memory()
            memory.refresh_system_index(force=True)
        except Exception:
            pass
    threading.Thread(target=_init, daemon=True).start()


@app.post("/api/memory/rebuild")
async def rebuild_memory() -> Dict[str, Any]:
    """Force rebuild the vector memory index with proper semantic embeddings."""
    from core.assistant import _get_memory
    try:
        memory = _get_memory()
        count = await asyncio.to_thread(memory.rebuild_index)
        return {"success": True, "chunks_indexed": count}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.get("/api/configs")
def get_configs() -> Dict[str, Any]:
    loader = get_config_loader()
    return loader.get_all_configs()


@app.get("/api/tools")
def get_tools() -> Dict[str, Any]:
    registry = _load_tools_registry()
    tools_data = []
    for tool_id, info in registry.get("tools", {}).items():
        tools_data.append({
            "id": tool_id,
            "name": info.get("name", tool_id),
            "description": info.get("description", ""),
            "category": info.get("category", "general"),
            "depends_on": info.get("depends_on", [])
        })

    return {
        "tools": tools_data,
        "examples": _extract_examples()
    }


@app.get("/api/uploads")
def list_uploads(session_id: Optional[str] = None) -> Dict[str, Any]:
    index = _prune_missing_uploads(_load_upload_index())
    index = _filter_index_by_session(index, session_id)
    return {"files": list(index.values())}


@app.get("/api/video/{video_id}")
def get_uploaded_video(video_id: str, session_id: Optional[str] = None) -> FileResponse:
    index = _prune_missing_uploads(_load_upload_index())
    record = index.get(video_id)
    if not record:
        raise HTTPException(status_code=404, detail="Uploaded video not found")

    record_session = record.get("session_id")
    if session_id and record_session and record_session != session_id:
        raise HTTPException(status_code=404, detail="Video not found for this session")

    path_value = record.get("path")
    if not path_value:
        raise HTTPException(status_code=404, detail="Video path is missing")

    path = Path(path_value)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Video file is missing")

    resolved = path.resolve()
    upload_root = UPLOAD_DIR.resolve()
    if upload_root not in resolved.parents:
        raise HTTPException(status_code=400, detail="Invalid upload path")

    if resolved.suffix.lower() not in _supported_video_extensions():
        raise HTTPException(status_code=400, detail="Selected file is not a supported video")

    media_type = record.get("content_type") or "video/mp4"
    filename = record.get("name") or resolved.name
    return FileResponse(path=resolved, media_type=media_type, filename=filename)


@app.post("/api/upload")
def upload_files(
    files: List[UploadFile] = File(...),
    session_id: Optional[str] = Form(None)
) -> Dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    results = []
    with UPLOAD_LOCK:
        index = _load_upload_index()

        for upload in files:
            file_id = uuid.uuid4().hex
            safe_name = _sanitize_filename(upload.filename or "upload")
            stored_name = f"{file_id}__{safe_name}"
            destination = UPLOAD_DIR / stored_name

            try:
                with destination.open("wb") as target:
                    shutil.copyfileobj(upload.file, target)
            finally:
                upload.file.close()

            size = destination.stat().st_size
            record = {
                "id": file_id,
                "name": safe_name,
                "stored_name": stored_name,
                "path": str(destination),
                "size": size,
                "content_type": upload.content_type,
                "uploaded_at": datetime.utcnow().isoformat() + "Z",
                "session_id": session_id
            }
            index[file_id] = record
            results.append(record)

        _save_upload_index(index)

    return {"files": results}


@app.delete("/api/upload/{file_id}")
def delete_upload(file_id: str, session_id: Optional[str] = None) -> Dict[str, Any]:
    with UPLOAD_LOCK:
        index = _load_upload_index()
        record = index.get(file_id)
        if not record:
            raise HTTPException(status_code=404, detail="Uploaded file not found")

        record_session = record.get("session_id")
        if session_id and record_session and record_session != session_id:
            raise HTTPException(status_code=404, detail="File not found for this session")

        path_value = record.get("path")
        if path_value:
            try:
                path = Path(path_value).resolve()
                upload_root = UPLOAD_DIR.resolve()
                if upload_root in path.parents and path.exists() and path.is_file():
                    path.unlink()
            except Exception:
                # Keep going; record deletion still happens.
                pass

        index.pop(file_id, None)
        _save_upload_index(index)

    _clear_active_video_refs(file_id=file_id, removed_path=record.get("path"))
    return {"success": True, "removed": record, "remaining": len(index)}


@app.post("/api/session/cleanup")
def cleanup_session(session_id: str) -> Dict[str, Any]:
    index = _load_upload_index()
    result = _cleanup_session_uploads(index, session_id)
    return result


@app.post("/api/process")
async def process_request(payload: ProcessRequest) -> Dict[str, Any]:
    if not payload.video_id and not payload.video_path:
        raise HTTPException(status_code=400, detail="video_id or video_path is required")

    resolved = _resolve_video(payload.video_id, payload.video_path, payload.session_id)
    path = resolved.get("path")
    index = resolved.get("index") or {}
    record = resolved.get("record")

    if not path:
        raise HTTPException(status_code=400, detail="Video path not resolved")

    error = _validate_video_path(path)
    if error:
        raise HTTPException(status_code=400, detail=error)

    result = await _run_edit_pipeline(
        payload.prompt,
        path,
        index,
        payload.session_id,
        payload.output_dir,
        payload.stock_items
    )

    if payload.cleanup_uploads:
        _cleanup_session_uploads(index, payload.session_id)

    session = _get_session_memory(payload.session_id)
    session["active_video_id"] = payload.video_id
    session["active_video_path"] = str(path)
    session["active_video_name"] = record.get("name") if record else path.name
    _append_history(session, "user", payload.prompt)

    if result.get("success"):
        _log_operation(session, {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "action": "edit_request",
            "prompt": payload.prompt,
            "outputs": result.get("outputs", {}),
            "summary": "Edit pipeline completed"
        })
        _append_history(session, "assistant", "Edit pipeline completed.")

    return result


@app.post("/api/chat/stream")
async def chat_stream(payload: ChatRequest):
    """
    SSE streaming endpoint for chat responses.
    Streams tokens as they arrive from the LLM for a ChatGPT/Claude-like experience.
    Returns Server-Sent Events with JSON data:
    - {"type": "token", "content": "..."} for each chunk
    - {"type": "done", "intent": "..."} when complete
    - {"type": "error", "content": "..."} on failure
    - {"type": "clarification", ...} when clarification is needed
    - {"type": "action", ...} for action intents that need processing
    """
    session = _get_session_memory(payload.session_id)
    history = session.get("history", [])

    _append_history(session, "user", payload.message)

    intent_result = classify_intent(payload.message, history)
    intent = intent_result.get("intent", "general_question")

    async def event_generator():
        # Handle clarification
        if intent == "needs_clarification":
            question = intent_result.get("question") or "Could you clarify what you want me to do?"
            data = json.dumps({
                "type": "clarification",
                "question": question,
                "options": intent_result.get("options") or [],
                "intent": intent,
            }, ensure_ascii=False)
            yield f"data: {data}\n\n"
            return

        # Handle action intents - these need processing, not streaming
        action_intents = {"edit_request", "extract_audio", "video_info", "transcribe_only", "captions_only",
                          "add_image_overlay", "add_text_overlay", "add_background_audio", "image_to_video"}
        if intent in action_intents:
            data = json.dumps({
                "type": "action",
                "intent": intent,
                "message": payload.message,
            }, ensure_ascii=False)
            yield f"data: {data}\n\n"
            return

        # Handle describe_edits directly
        if intent == "describe_edits":
            ops = session.get("operations", [])
            if not ops:
                reply = "No edits or processing steps have been run yet in this session."
            else:
                lines = ["Here is what I have done so far:\n"]
                for op in ops[-6:]:
                    timestamp = op.get("timestamp", "")
                    action = op.get("action", "operation")
                    summary = op.get("summary") or op.get("prompt") or ""
                    outputs = op.get("outputs", {})
                    output_list = ", ".join(sorted(outputs.keys())) if outputs else "no outputs"
                    lines.append(f"- **{action}** ({summary}); outputs: {output_list}")
                    if timestamp:
                        lines[-1] += f" [{timestamp}]"
                reply = "\n".join(lines)
            _append_history(session, "assistant", reply)
            # Stream the describe_edits response token by token for consistent UX
            for char in reply:
                data = json.dumps({"type": "token", "content": char}, ensure_ascii=False)
                yield f"data: {data}\n\n"
            data = json.dumps({"type": "done", "intent": intent}, ensure_ascii=False)
            yield f"data: {data}\n\n"
            return

        # QA streaming
        memory_summary = _build_memory_summary(session)
        full_response = []
        try:
            for chunk in generate_response_stream(
                payload.message,
                history,
                memory_summary,
                payload.session_id,
            ):
                full_response.append(chunk)
                data = json.dumps({"type": "token", "content": chunk}, ensure_ascii=False)
                yield f"data: {data}\n\n"
        except Exception as exc:
            error_data = json.dumps({"type": "error", "content": str(exc)}, ensure_ascii=False)
            yield f"data: {error_data}\n\n"
            return

        # Save the full response to history
        full_text = "".join(full_response).strip()
        if full_text:
            _append_history(session, "assistant", full_text)

        data = json.dumps({"type": "done", "intent": intent}, ensure_ascii=False)
        yield f"data: {data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/chat")
async def chat_request(payload: ChatRequest) -> Dict[str, Any]:
    session = _get_session_memory(payload.session_id)
    history = session.get("history", [])
    intent_result = classify_intent(payload.message, history)
    intent = intent_result.get("intent", "general_question")

    _append_history(session, "user", payload.message)

    if intent == "needs_clarification":
        question = intent_result.get("question") or "Could you clarify what you want me to do?"
        return {
            "success": False,
            "needs_clarification": True,
            "clarification": {
                "question": question,
                "options": intent_result.get("options") or []
            },
            "intent": intent
        }

    action_intents = {"edit_request", "extract_audio", "video_info", "transcribe_only", "captions_only",
                      "add_image_overlay", "add_text_overlay", "add_background_audio", "image_to_video"}
    if intent in action_intents:
        resolved = _resolve_video(payload.video_id, payload.video_path, payload.session_id)
        path = resolved.get("path")
        record = resolved.get("record")
        index = resolved.get("index") or {}

        if not path:
            return {
                "success": False,
                "needs_clarification": True,
                "clarification": {
                    "question": "Which video should I use? Upload or select an active video first.",
                    "options": []
                },
                "intent": intent
            }

        error = _validate_video_path(path)
        if error:
            return {
                "success": False,
                "errors": [error],
                "intent": intent
            }

        session["active_video_id"] = payload.video_id
        session["active_video_path"] = str(path)
        session["active_video_name"] = record.get("name") if record else path.name

        if intent == "video_info":
            from tools.ffmpeg_tool import FFmpegTool

            ffmpeg = FFmpegTool()
            info = ffmpeg.get_video_info(str(path))
            if not info.get("success"):
                return {
                    "success": False,
                    "errors": [info.get("error", "Failed to read video info")],
                    "intent": intent
                }
            reply = _format_video_info(info)
            _append_history(session, "assistant", reply)
            _log_operation(session, {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "action": "video_info",
                "summary": "Retrieved video metadata",
                "outputs": {}
            })
            return {
                "success": True,
                "reply": reply,
                "video_info": info,
                "intent": intent
            }

        if intent == "extract_audio":
            result = _run_audio_extract(path, payload.output_dir)
            if result.get("success"):
                _log_operation(session, {
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "action": "extract_audio",
                    "summary": "Extracted audio only",
                    "outputs": result.get("outputs", {})
                })
                result["reply"] = "Audio extracted successfully."
                _append_history(session, "assistant", result["reply"])
            return {**result, "intent": intent}

        if intent == "transcribe_only":
            result = _run_transcribe_only(path, payload.output_dir)
            if result.get("success"):
                _log_operation(session, {
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "action": "transcribe_only",
                    "summary": "Transcription generated",
                    "outputs": result.get("outputs", {})
                })
                preview = result.get("preview")
                reply = "Transcription complete."
                if preview:
                    reply = f"{reply}\nPreview: {preview}"
                _append_history(session, "assistant", reply)
                result["reply"] = reply
            return {**result, "intent": intent}

        if intent == "captions_only":
            result = _run_captions_only(path, payload.message, payload.output_dir)
            if result.get("success"):
                _log_operation(session, {
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "action": "captions_only",
                    "summary": "Generated captions file",
                    "outputs": result.get("outputs", {})
                })
                _append_history(session, "assistant", "Captions file generated.")
                result["reply"] = "Captions file generated."
            return {**result, "intent": intent}

        if intent in ("edit_request", "add_image_overlay", "add_text_overlay", "add_background_audio"):
            result = await _run_edit_pipeline(
                payload.message,
                path,
                index,
                payload.session_id,
                payload.output_dir,
                payload.stock_items
            )
            if result.get("needs_clarification"):
                return {**result, "intent": intent}

            if result.get("success"):
                _log_operation(session, {
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "action": "edit_request",
                    "summary": "Edit pipeline completed",
                    "prompt": payload.message,
                    "outputs": result.get("outputs", {})
                })
                _append_history(session, "assistant", "Edit pipeline completed.")
            return {**result, "intent": intent}

    if intent == "describe_edits":
        ops = session.get("operations", [])
        if not ops:
            reply = "No edits or processing steps have been run yet in this session."
        else:
            lines = ["Here is what I have done so far:"]
            for op in ops[-6:]:
                timestamp = op.get("timestamp", "")
                action = op.get("action", "operation")
                summary = op.get("summary") or op.get("prompt") or ""
                outputs = op.get("outputs", {})
                output_list = ", ".join(sorted(outputs.keys())) if outputs else "no outputs"
                lines.append(f"- {timestamp}: {action} ({summary}); outputs: {output_list}")
            reply = "\n".join(lines)
        _append_history(session, "assistant", reply)
        return {
            "success": True,
            "reply": reply,
            "intent": intent
        }

    memory_summary = _build_memory_summary(session)
    reply = generate_response(payload.message, history, memory_summary, payload.session_id)
    _append_history(session, "assistant", reply)
    return {
        "success": True,
        "reply": reply,
        "intent": intent
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.web_server:app",
        host="127.0.0.1",
        port=8000,
        reload=True
    )
