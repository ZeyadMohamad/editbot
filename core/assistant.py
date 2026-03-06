"""
Assistant layer for EditBot.

Implements a 3-model architecture:
1) Head model: classify request route (QA vs implementation)
2) QA model: answer with ReAct-style retrieval over vector memory (RAG)
3) Implementation model: classify actionable tool intent
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from core.logging import setup_logger
from core.prompt_loader import load_prompt_text
from core.vector_memory import LocalVectorStore, SearchResult, chunk_text, filter_cli_chunks

logger = setup_logger("assistant")

PROJECT_ROOT = Path(__file__).parent.parent
MEMORY_DB_PATH = PROJECT_ROOT / "workspace" / "memory" / "chroma_db"


def _parse_bool_flag(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled", ""}:
        return False
    return default


_DEFAULT_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL") or "tinyllama"
HEAD_MODEL = os.getenv("EDITBOT_HEAD_MODEL") or _DEFAULT_OLLAMA_MODEL
QA_MODEL = os.getenv("EDITBOT_QA_MODEL") or _DEFAULT_OLLAMA_MODEL
IMPLEMENTATION_MODEL = os.getenv("EDITBOT_IMPLEMENTATION_MODEL") or _DEFAULT_OLLAMA_MODEL
_GLOBAL_REASONING = _parse_bool_flag(
    os.getenv("EDITBOT_REASONING"),
    default=_parse_bool_flag(os.getenv("OLLAMA_REASONING"), default=False),
)
HEAD_REASONING = _parse_bool_flag(os.getenv("EDITBOT_HEAD_REASONING"), default=_GLOBAL_REASONING)
QA_REASONING = _parse_bool_flag(os.getenv("EDITBOT_QA_REASONING"), default=_GLOBAL_REASONING)
IMPLEMENTATION_REASONING = _parse_bool_flag(
    os.getenv("EDITBOT_IMPLEMENTATION_REASONING"),
    default=_GLOBAL_REASONING,
)
QA_REACT_ENABLED = _parse_bool_flag(os.getenv("EDITBOT_QA_REACT"), default=False)
QA_MAX_TOKENS = int(os.getenv("EDITBOT_QA_MAX_TOKENS", "512"))

PROMPT_HEAD_ROUTER = "assistant_head_router_system.txt"
PROMPT_IMPLEMENTATION_INTENT = "assistant_implementation_intent_system.txt"
PROMPT_REACT_QUERY = "assistant_react_query_system.txt"
PROMPT_QA_BRIEF = "assistant_qa_brief_system.txt"
PROMPT_QA_STANDARD = "assistant_qa_standard_system.txt"

SYSTEM_INDEX_PATTERNS = [
    "README.md",
    "docs/*.md",
    "configs/*.json",
    "registry/*.json",
    "prompts/*.txt",
    "prompts/*.md",
    "core/*.py",
    "tools/*.py",
    "app/*.py",
    "web/*.html",
    "web/static/*.js",
    "web/static/*.css",
    "tests/*.py",
]

PROJECT_TEXT_EXTENSIONS = {
    ".py", ".json", ".md", ".txt", ".html", ".css", ".js", ".ini", ".toml", ".yaml", ".yml"
}

PROJECT_EXCLUDED_DIR_NAMES = {
    ".git",
    ".venv",
    ".pip_cache",
    ".models",
    "__pycache__",
    "node_modules",
    "reference videos",
    "fine-tune",
    "workspace",
    "output",
    "logs",
}

ACTION_INTENTS = {
    "edit_request",
    "extract_audio",
    "video_info",
    "transcribe_only",
    "captions_only",
}

ACTION_ROUTE_KEYWORDS = (
    "add captions",
    "caption",
    "subtitle",
    "edit",
    "cut",
    "trim",
    "stock",
    "b-roll",
    "b roll",
    "transition",
    "rotate",
    "rotation",
    "clockwise",
    "counterclockwise",
    "anticlockwise",
    "render",
    "transcribe",
    "transcription",
    "extract audio",
    "video info",
    "metadata",
)

EDIT_OPERATION_KEYWORDS = (
    "add captions",
    "caption",
    "subtitle",
    "edit",
    "cut",
    "trim",
    "remove silence",
    "silence",
    "filler",
    "stock",
    "b-roll",
    "b roll",
    "broll",
    "transition",
    "rotate",
    "rotation",
    "clockwise",
    "counterclockwise",
    "anticlockwise",
    "cross dissolve",
    "crossfade",
    "xfade",
    "render",
    "extract audio",
    "audio only",
    "export audio",
    "transcribe",
    "transcription",
    "speech to text",
    "subtitle file",
    "captions file",
    "srt",
    "vtt",
    "ass file",
    "splice",
    "insert",
    "overlay",
)

VIDEO_INFO_METRIC_KEYWORDS = (
    "video info",
    "video metadata",
    "metadata",
    "resolution",
    "fps",
    "frame rate",
    "duration",
    "codec",
    "bitrate",
    "audio sample rate",
    "audio channels",
)

VIDEO_INFO_INTENT_WORDS = (
    "what",
    "show",
    "tell",
    "get",
    "give",
    "display",
    "check",
    "read",
    "find",
    "info",
    "details",
    "stats",
)


def _is_video_info_request(text: str) -> bool:
    if not text:
        return False

    # If the prompt contains explicit editing verbs, prefer edit execution.
    if any(keyword in text for keyword in EDIT_OPERATION_KEYWORDS):
        return False

    # Explicit metadata phrasing should always route to video info.
    if "video info" in text or "video metadata" in text:
        return True

    if not any(keyword in text for keyword in VIDEO_INFO_METRIC_KEYWORDS):
        return False

    if _looks_like_question(text):
        return True

    return any(keyword in text for keyword in VIDEO_INFO_INTENT_WORDS)


def _extract_json_block(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    text = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None

    candidate = match.group(0)
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None
    return None


def _format_history(history: Optional[List[Dict[str, Any]]], limit: int = 8) -> str:
    if not history:
        return ""
    lines: List[str] = []
    for item in history[-limit:]:
        role = str(item.get("role", "user")).strip().lower()
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _fallback_intent(message: str) -> Dict[str, Any]:
    text = (message or "").strip().lower()
    if not text:
        return {"intent": "general_question", "route": "qa", "confidence": 0.3}

    # Greetings and social
    greetings = {"hello", "hi", "hey", "yo", "sup", "good morning", "good afternoon",
                 "good evening", "thanks", "thank you", "thx", "ok", "okay", "cool",
                 "nice", "great", "awesome", "bye", "goodbye", "see you"}
    if text in greetings or any(text.startswith(g + " ") for g in greetings):
        return {"intent": "general_question", "route": "qa", "confidence": 0.9}

    # History/operations questions
    if any(k in text for k in ["what did you do", "what edits", "show edits", "history",
                                 "operations done", "what happened", "what was done",
                                 "previous operations", "show me what", "what changes",
                                 "list operations", "what have you"]):
        return {"intent": "describe_edits", "route": "qa", "confidence": 0.9}

    # Why questions about operations
    if text.startswith("why ") and any(k in text for k in ["cut", "trim", "remove", "silence",
                                                            "caption", "transition", "duration"]):
        return {"intent": "describe_edits", "route": "qa", "confidence": 0.85}

    if _is_video_info_request(text):
        return {"intent": "video_info", "route": "implementation", "confidence": 0.8}

    if any(k in text for k in ["extract audio", "audio only", "export audio"]):
        return {"intent": "extract_audio", "route": "implementation", "confidence": 0.8}

    if any(k in text for k in ["transcribe", "transcription", "speech to text"]) and "caption" not in text:
        return {"intent": "transcribe_only", "route": "implementation", "confidence": 0.75}

    if any(k in text for k in ["srt", "vtt", "subtitle file", "captions file", "ass file"]):
        return {"intent": "captions_only", "route": "implementation", "confidence": 0.75}

    if any(k in text for k in ACTION_ROUTE_KEYWORDS):
        return {"intent": "edit_request", "route": "implementation", "confidence": 0.7}

    # Question detection - be generous, route to QA
    question_like = "?" in text or text.startswith(
        ("what ", "how ", "which ", "can you", "do you", "is ", "are ",
         "does ", "did ", "why ", "when ", "where ", "who ", "tell me",
         "explain", "describe", "show me", "help me")
    )
    if question_like:
        return {"intent": "general_question", "route": "qa", "confidence": 0.8}

    # Capability questions
    if _is_capability_question(text):
        return {"intent": "general_question", "route": "qa", "confidence": 0.85}

    # Short messages default to QA with decent confidence
    if len(text.split()) <= 6:
        return {"intent": "general_question", "route": "qa", "confidence": 0.7}

    return {"intent": "general_question", "route": "qa", "confidence": 0.6}


def _fast_route_intent(message: str) -> Optional[Dict[str, Any]]:
    """
    Fast rule-based routing. Aggressively routes obvious patterns to avoid
    slow LLM head-model calls. Returns None only when truly ambiguous.
    """
    fallback = _fallback_intent(message)
    confidence = float(fallback.get("confidence", 0.0) or 0.0)

    # Lower threshold: route anything with reasonable confidence fast
    if confidence < 0.6:
        return None

    route = str(fallback.get("route", "qa"))
    intent = str(fallback.get("intent", "general_question"))

    # Question-like messages almost always go to QA
    if _looks_like_question(message) and route == "qa":
        qa_intent = "describe_edits" if intent == "describe_edits" else "general_question"
        return {
            "intent": qa_intent,
            "route": "qa",
            "confidence": max(confidence, 0.75),
            "model": "fast_rules",
        }

    if route == "qa":
        qa_intent = "describe_edits" if intent == "describe_edits" else "general_question"
        return {
            "intent": qa_intent,
            "route": "qa",
            "confidence": confidence,
            "model": "fast_rules",
        }
    if intent not in ACTION_INTENTS:
        intent = "edit_request"
    return {
        "intent": intent,
        "route": "implementation",
        "confidence": confidence,
        "model": "fast_rules",
    }


def _looks_like_question(message: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    if "?" in text:
        return True
    return text.startswith(
        (
            "what ",
            "how ",
            "which ",
            "why ",
            "when ",
            "where ",
            "can you",
            "do you",
            "is ",
            "are ",
            "does ",
            "did ",
        )
    )


def _is_capability_question(message: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    return any(
        token in text
        for token in [
            "what can you do",
            "what do you do",
            "capabilities",
            "features",
            "supported",
            "available tools",
            "which tools",
            "help me with",
        ]
    )


def _ollama_chat(
    model: str,
    messages: List[Dict[str, str]],
    json_mode: bool = False,
    temperature: float = 0.1,
    reasoning: Optional[bool] = None,
    num_predict: Optional[int] = None,
) -> Optional[str]:
    try:
        import ollama  # type: ignore
    except Exception as exc:
        logger.warning(f"Ollama import failed: {exc}")
        return None

    options: Dict[str, Any] = {"temperature": temperature}
    if num_predict is not None:
        options["num_predict"] = int(num_predict)

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "options": options,
    }
    if json_mode:
        payload["format"] = "json"
    if reasoning is True:
        payload["think"] = True

    try:
        response = ollama.chat(**payload)
        return response.get("message", {}).get("content", "").strip()
    except Exception as exc:
        # Backward compatibility: older ollama clients may not support `think`.
        if "think" in payload:
            fallback_payload = dict(payload)
            fallback_payload.pop("think", None)
            try:
                response = ollama.chat(**fallback_payload)
                return response.get("message", {}).get("content", "").strip()
            except Exception as fallback_exc:
                logger.warning(f"Ollama chat failed for model {model}: {fallback_exc}")
                return None
        logger.warning(f"Ollama chat failed for model {model}: {exc}")
        return None


def _ollama_chat_stream(
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.1,
    reasoning: Optional[bool] = None,
    num_predict: Optional[int] = None,
):
    """
    Streaming version of _ollama_chat. Yields text chunks as they arrive.
    """
    try:
        import ollama  # type: ignore
    except Exception as exc:
        logger.warning(f"Ollama import failed: {exc}")
        return

    options: Dict[str, Any] = {"temperature": temperature}
    if num_predict is not None:
        options["num_predict"] = int(num_predict)

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "options": options,
        "stream": True,
    }
    if reasoning is True:
        payload["think"] = True

    try:
        stream = ollama.chat(**payload)
        for chunk in stream:
            content = chunk.get("message", {}).get("content", "")
            if content:
                yield content
    except Exception as exc:
        if "think" in payload:
            fallback_payload = dict(payload)
            fallback_payload.pop("think", None)
            try:
                stream = ollama.chat(**fallback_payload)
                for chunk in stream:
                    content = chunk.get("message", {}).get("content", "")
                    if content:
                        yield content
                return
            except Exception:
                pass
        logger.warning(f"Ollama streaming failed for model {model}: {exc}")


def _should_use_retrieval(message: str) -> bool:
    """
    Determine if RAG retrieval should be used for this message.
    Be generous - retrieval improves most responses.
    """
    text = (message or "").strip().lower()
    if not text:
        return False
    # Skip retrieval only for very short social messages
    if _is_brief_conversation_message(text):
        return False
    # Everything else benefits from retrieval
    return True


def _is_brief_conversation_message(message: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    tokens = text.split()
    if len(tokens) > 8:
        return False
    short_social = {
        "hello",
        "hi",
        "hey",
        "yo",
        "sup",
        "thanks",
        "thank you",
        "thx",
        "ok",
        "okay",
        "cool",
        "nice",
    }
    if text in short_social:
        return True
    return text.startswith(("hello ", "hi ", "hey ", "thanks ", "thank you "))


def _head_route(message: str, history: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    caution = (
        "Do not include chain-of-thought. Return final JSON only."
        if not HEAD_REASONING
        else ""
    )
    system_prompt = load_prompt_text(
        PROMPT_HEAD_ROUTER,
        fallback=(
            "You are the head router for EditBot. "
            "Return strict JSON with keys: route, confidence, question, options."
        ),
    )
    if caution:
        system_prompt = f"{system_prompt} {caution}"

    user_payload = {
        "message": message,
        "recent_history": _format_history(history, limit=8),
    }
    content = _ollama_chat(
        model=HEAD_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        json_mode=True,
        temperature=0.0,
        reasoning=HEAD_REASONING,
    )
    parsed = _extract_json_block(content or "")
    if not parsed:
        fallback = _fallback_intent(message)
        return {
            "route": fallback.get("route", "implementation"),
            "confidence": fallback.get("confidence", 0.5),
            "question": "",
            "options": [],
        }

    route = str(parsed.get("route", "")).strip().lower()
    if route not in {"qa", "implementation", "needs_clarification"}:
        fallback = _fallback_intent(message)
        route = fallback.get("route", "implementation")

    confidence = parsed.get("confidence", 0.5)
    try:
        confidence = float(confidence)
    except Exception:
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    question = str(parsed.get("question", "")).strip()
    options = parsed.get("options") if isinstance(parsed.get("options"), list) else []
    options = [str(x).strip() for x in options if str(x).strip()][:4]
    return {
        "route": route,
        "confidence": confidence,
        "question": question,
        "options": options,
    }


def _implementation_intent(message: str, history: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    caution = (
        "Do not include chain-of-thought. Return final JSON only."
        if not IMPLEMENTATION_REASONING
        else ""
    )
    system_prompt = load_prompt_text(
        PROMPT_IMPLEMENTATION_INTENT,
        fallback=(
            "You classify implementation requests for EditBot. "
            "Return strict JSON with keys: intent, question, options."
        ),
    )
    if caution:
        system_prompt = f"{system_prompt} {caution}"

    user_payload = {
        "message": message,
        "recent_history": _format_history(history, limit=8),
    }
    content = _ollama_chat(
        model=IMPLEMENTATION_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        json_mode=True,
        temperature=0.0,
        reasoning=IMPLEMENTATION_REASONING,
    )
    parsed = _extract_json_block(content or "")
    if not parsed:
        fallback = _fallback_intent(message)
        return {
            "intent": fallback.get("intent", "edit_request"),
            "question": "",
            "options": [],
        }

    intent = str(parsed.get("intent", "")).strip().lower()
    if intent not in ACTION_INTENTS and intent != "needs_clarification":
        fallback = _fallback_intent(message)
        intent = fallback.get("intent", "edit_request")
        if intent not in ACTION_INTENTS:
            intent = "edit_request"

    question = str(parsed.get("question", "")).strip()
    options = parsed.get("options") if isinstance(parsed.get("options"), list) else []
    options = [str(x).strip() for x in options if str(x).strip()][:4]
    return {
        "intent": intent,
        "question": question,
        "options": options,
    }


def _load_json_file(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_knowledge_pack_text(project_root: Path) -> str:
    """
    Build a high-signal project knowledge document that is always indexed into RAG.
    """
    tools_registry = _load_json_file(project_root / "registry" / "tools.json")
    config_map = _load_json_file(project_root / "registry" / "config_map.json")
    formats = _load_json_file(project_root / "configs" / "supported_formats.json")
    languages = _load_json_file(project_root / "configs" / "supported_languages.json")
    silence_cfg = _load_json_file(project_root / "configs" / "silence_cutter.json")

    tools = tools_registry.get("tools", {}) if isinstance(tools_registry.get("tools"), dict) else {}
    pipelines = tools_registry.get("pipelines", {}) if isinstance(tools_registry.get("pipelines"), dict) else {}
    categories = tools_registry.get("categories", {}) if isinstance(tools_registry.get("categories"), dict) else {}

    lines: List[str] = []
    lines.append("# EditBot System Knowledge Pack")
    lines.append("")
    lines.append("This document is generated from local project files and indexed into the vector database.")
    lines.append("Use it as authoritative context for capability and architecture questions.")
    lines.append("")

    lines.append("## Core Purpose")
    lines.append("- EditBot automates video-editing workflows driven by natural-language prompts.")
    lines.append("- It supports captioning, transcription, silence/filler cutting, transitions, stock footage composition, and media rotation.")
    lines.append("- It exposes a FastAPI web UI and a CLI entrypoint.")
    lines.append("")

    lines.append("## User Interfaces")
    lines.append("- Web app: `/`")
    lines.append("- Chat endpoint: `/api/chat`")
    lines.append("- Processing endpoint: `/api/process`")
    lines.append("- Upload endpoints: `/api/upload`, `/api/uploads`, `/api/session/cleanup`")
    lines.append("- Position helper UI: `/position-helper`")
    lines.append("- Dynamic uploaded-video streaming endpoint: `/api/video/{video_id}`")
    lines.append("")

    lines.append("## LLM Routing Architecture")
    lines.append("- Head model (`EDITBOT_HEAD_MODEL`): route user message to QA vs implementation")
    lines.append("- QA model (`EDITBOT_QA_MODEL`): answer using retrieval over vector memory (RAG + ReAct-style extra queries)")
    lines.append("- Implementation model (`EDITBOT_IMPLEMENTATION_MODEL`): classify executable intent")
    lines.append("- Clarification model defaults to head/QA env chain in `core/clarifier.py`")
    lines.append("- Reasoning flags are env-controlled per model and default to off.")
    lines.append("")

    lines.append("## Tool Categories")
    if categories:
        for key, value in categories.items():
            lines.append(f"- `{key}`: {value}")
    else:
        lines.append("- No categories found in registry/tools.json")
    lines.append("")

    lines.append("## Registered Tools")
    if not tools:
        lines.append("- No tools found in registry/tools.json")
    for tool_id, info in sorted(tools.items(), key=lambda kv: kv[0]):
        name = info.get("name", tool_id)
        description = info.get("description", "")
        module = info.get("module", "")
        method = info.get("method", "")
        category = info.get("category", "general")
        depends_on = info.get("depends_on", []) or []
        config_files = info.get("config_files", []) or []
        inputs = info.get("inputs", {}) or {}
        outputs = info.get("outputs", {}) or {}

        lines.append(f"### Tool: {tool_id}")
        lines.append(f"- Name: {name}")
        lines.append(f"- Description: {description}")
        lines.append(f"- Module/Method: `{module}.{method}`")
        lines.append(f"- Category: `{category}`")
        lines.append(f"- Depends on: {', '.join(depends_on) if depends_on else 'none'}")
        lines.append(f"- Config files: {', '.join(config_files) if config_files else 'none'}")
        lines.append("- Inputs:")
        if inputs:
            for input_name, input_spec in inputs.items():
                i_type = input_spec.get("type", "any")
                i_req = bool(input_spec.get("required", False))
                i_default = input_spec.get("default", None)
                i_desc = input_spec.get("description", "")
                req_text = "required" if i_req else "optional"
                default_text = f", default={i_default}" if i_default is not None else ""
                lines.append(
                    f"  - `{input_name}` ({i_type}, {req_text}{default_text}): {i_desc}".rstrip()
                )
        else:
            lines.append("  - none")
        lines.append("- Outputs:")
        if outputs:
            for output_name, output_spec in outputs.items():
                o_type = output_spec.get("type", "any")
                o_desc = output_spec.get("description", "")
                lines.append(f"  - `{output_name}` ({o_type}): {o_desc}")
        else:
            lines.append("  - none")
        lines.append("")

    lines.append("## Pipelines (registry/tools.json)")
    if pipelines:
        for pipeline_name, pipeline_info in sorted(pipelines.items(), key=lambda kv: kv[0]):
            steps = pipeline_info.get("steps", []) or []
            description = pipeline_info.get("description", "")
            lines.append(f"- `{pipeline_name}`: {description}")
            lines.append(f"  - Steps: {', '.join(steps) if steps else 'none'}")
    else:
        lines.append("- No pipelines found.")
    lines.append("")

    lines.append("## Formats And Language Support")
    video_input = ((formats.get("video_extensions") or {}).get("input") or [])
    audio_input = ((formats.get("audio_extensions") or {}).get("input") or [])
    image_input = ((formats.get("image_extensions") or {}).get("input") or [])
    subtitle_output = ((formats.get("subtitle_extensions") or {}).get("output") or [])
    lines.append(f"- Supported input video extensions: {', '.join(video_input) if video_input else 'unknown'}")
    lines.append(f"- Supported input audio extensions: {', '.join(audio_input) if audio_input else 'unknown'}")
    lines.append(f"- Supported input image extensions: {', '.join(image_input) if image_input else 'unknown'}")
    lines.append(f"- Supported subtitle output extensions: {', '.join(subtitle_output) if subtitle_output else 'unknown'}")
    lines.append("")

    supported_lang = languages.get("languages", {}) if isinstance(languages.get("languages"), dict) else {}
    if supported_lang:
        lang_codes = sorted(supported_lang.keys())
        lines.append(f"- Supported language codes (from config): {', '.join(lang_codes)}")
    else:
        lines.append("- Supported language config not found or empty.")
    lines.append("")

    lines.append("## Practical Limits And Requirements")
    lines.append("- FFmpeg must be installed and available on PATH.")
    lines.append("- Whisper transcription quality/speed depends on selected model size and GPU/CPU availability.")
    lines.append("- `apply_transitions` requires at least two clips or two segments.")
    lines.append("- Stock overlay requires `start_time`; image overlays require explicit duration or end_time.")
    lines.append("- Stock insert requires `start_time` and a resolvable stock duration.")
    lines.append("- Media rotation accepts clockwise degrees or natural-language left/right turns.")
    lines.append("- Silence cutter requires a valid WAV audio input for wave-based analysis.")
    lines.append("- Manual cut segments override automatic silence/filler detection when provided.")
    lines.append("- Caption generation expects word-level timestamps; no words means no ASS output.")
    lines.append("- ASS subtitle burn requires valid source video and subtitle path.")
    lines.append("")

    defaults = silence_cfg.get("defaults", {}) if isinstance(silence_cfg.get("defaults"), dict) else {}
    if defaults:
        lines.append("## Silence Cutter Defaults (from config)")
        for key, value in defaults.items():
            lines.append(f"- `{key}`: {value}")
        lines.append("")

    keyword_mapping = config_map.get("keyword_mapping", {}) if isinstance(config_map.get("keyword_mapping"), dict) else {}
    lines.append("## Config Loader Behavior")
    lines.append("- ConfigLoader maps prompt keywords/intents to config files using `registry/config_map.json`.")
    lines.append(f"- Keyword-mapped config files: {', '.join(sorted(keyword_mapping.keys())) if keyword_mapping else 'none'}")
    intent_map = config_map.get("intent_to_configs", {}) if isinstance(config_map.get("intent_to_configs"), dict) else {}
    if intent_map:
        lines.append("- Intent-to-config mapping:")
        for intent, cfgs in sorted(intent_map.items(), key=lambda kv: kv[0]):
            cfg_list = ", ".join(cfgs or [])
            lines.append(f"  - `{intent}` -> {cfg_list}")
    lines.append("")

    lines.append("## Output Artifacts")
    lines.append("- Common generated artifacts include:")
    lines.append("  - extracted audio WAV files")
    lines.append("  - transcript JSON files")
    lines.append("  - ASS/SRT/VTT subtitle files")
    lines.append("  - cut list JSON files")
    lines.append("  - final processed video files")
    lines.append("")

    lines.append("## Position Helper")
    lines.append("- The Position Helper page shows a dynamic uploaded video and live cursor coordinates.")
    lines.append("- It returns both pixel and percentage coordinates for overlay placement.")
    lines.append("- It is meant for stock footage/image overlay positioning workflows.")
    lines.append("")

    return "\n".join(lines).strip() + "\n"


class AssistantMemory:
    def __init__(self, db_path: Path):
        self.store = LocalVectorStore(db_path=db_path, dimension=384)
        self._index_lock = threading.RLock()
        self._indexed_once = False
        self._last_refresh = 0.0
        self._knowledge_pack_path = PROJECT_ROOT / "docs" / "system_knowledge_pack.md"

    def _is_excluded(self, path: Path) -> bool:
        parts = {part.lower() for part in path.parts}
        for excluded_name in PROJECT_EXCLUDED_DIR_NAMES:
            if excluded_name.lower() in parts:
                return True
        # Never index uploaded user media as text context.
        uploads_dir = PROJECT_ROOT / "workspace" / "uploads"
        try:
            if uploads_dir.resolve() in path.resolve().parents:
                return True
        except Exception:
            pass
        return False

    def _collect_project_text_files(self) -> List[Path]:
        files: List[Path] = []
        for path in PROJECT_ROOT.rglob("*"):
            if not path.is_file():
                continue
            if self._is_excluded(path):
                continue
            if path.suffix.lower() not in PROJECT_TEXT_EXTENSIONS:
                continue
            files.append(path)
        return sorted(files)

    def _upsert_knowledge_pack(self) -> int:
        text = _build_knowledge_pack_text(PROJECT_ROOT)
        self._knowledge_pack_path.parent.mkdir(parents=True, exist_ok=True)
        self._knowledge_pack_path.write_text(text, encoding="utf-8")

        fingerprint = hashlib.sha1(text.encode("utf-8")).hexdigest()
        chunks = filter_cli_chunks(chunk_text(text, max_chars=1400, overlap=220))
        return self.store.upsert_source_chunks(
            source_key="virtual://system_knowledge_pack",
            chunks=chunks,
            metadata_base={
                "type": "system_file",
                "path": str(self._knowledge_pack_path.as_posix()),
                "name": self._knowledge_pack_path.name,
                "source_kind": "knowledge_pack",
            },
            fingerprint=fingerprint,
        )

    def refresh_system_index(self, force: bool = False) -> int:
        now = time.time()
        if not force and self._indexed_once and (now - self._last_refresh) < 90:
            return 0

        with self._index_lock:
            # Double-check in lock to avoid duplicate scans.
            now = time.time()
            if not force and self._indexed_once and (now - self._last_refresh) < 90:
                return 0

            files: List[Path] = []
            for pattern in SYSTEM_INDEX_PATTERNS:
                files.extend(sorted(PROJECT_ROOT.glob(pattern)))
            files.extend(self._collect_project_text_files())

            # Remove duplicates while preserving order.
            seen = set()
            unique_files: List[Path] = []
            for path in files:
                if path in seen or not path.is_file():
                    continue
                seen.add(path)
                unique_files.append(path)

            indexed = self._upsert_knowledge_pack()
            for path in unique_files:
                try:
                    # Keep indexing light and robust.
                    if path.stat().st_size > 1_500_000:
                        continue
                except Exception:
                    continue
                indexed += self.store.index_file(path)

            self._indexed_once = True
            self._last_refresh = time.time()
            if indexed:
                logger.info(f"System memory index refreshed: +{indexed} chunks")
            return indexed

    def rebuild_index(self) -> int:
        """
        Force a complete rebuild of the vector store.
        Deletes the existing collection and re-indexes everything from scratch.
        This is needed when switching embedding methods (e.g., hash -> semantic).
        """
        with self._index_lock:
            try:
                self.store.client.delete_collection(self.store.collection_name)
                logger.info("Deleted old ChromaDB collection for rebuild")
            except Exception:
                pass
            # Recreate the collection
            try:
                from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
                emb_fn = DefaultEmbeddingFunction()
                self.store.collection = self.store.client.get_or_create_collection(
                    name=self.store.collection_name,
                    metadata={"hnsw:space": "cosine"},
                    embedding_function=emb_fn,
                )
            except Exception:
                self.store.collection = self.store.client.get_or_create_collection(
                    name=self.store.collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
            self._indexed_once = False
            self._last_refresh = 0.0
            return self.refresh_system_index(force=True)

    def add_chat_turn(self, session_id: Optional[str], role: str, content: str) -> Optional[str]:
        text = (content or "").strip()
        if not text:
            return None
        sid = session_id or "default"
        event_id = uuid4().hex
        payload = f"chat[{role}] session={sid}: {text}"
        return self.store.add_entry(
            text=payload,
            metadata={
                "type": "chat_turn",
                "session_id": sid,
                "role": role,
                "event_id": event_id,
            },
            source_key=f"chat::{sid}::{event_id}",
        )

    def add_operation(self, session_id: Optional[str], operation: Dict[str, Any]) -> Optional[str]:
        sid = session_id or "default"
        action = str(operation.get("action", "operation")).strip()
        summary = str(operation.get("summary") or operation.get("prompt") or "").strip()
        timestamp = str(operation.get("timestamp", "")).strip()
        outputs = operation.get("outputs") if isinstance(operation.get("outputs"), dict) else {}
        outputs_desc = ", ".join(f"{k}={v}" for k, v in sorted(outputs.items()))
        parts = [f"session={sid}", f"action={action}"]
        if timestamp:
            parts.append(f"time={timestamp}")
        if summary:
            parts.append(f"summary={summary}")
        if outputs_desc:
            parts.append(f"outputs={outputs_desc}")
        payload = "operation: " + " | ".join(parts)

        event_id = operation.get("timestamp") or uuid4().hex
        return self.store.add_entry(
            text=payload,
            metadata={
                "type": "operation",
                "session_id": sid,
                "action": action,
                "timestamp": timestamp,
                "event_id": str(event_id),
            },
            source_key=f"operation::{sid}::{action}::{event_id}",
        )


_MEMORY: Optional[AssistantMemory] = None
_MEMORY_LOCK = threading.Lock()


def _get_memory() -> AssistantMemory:
    global _MEMORY
    if _MEMORY is None:
        with _MEMORY_LOCK:
            if _MEMORY is None:
                _MEMORY = AssistantMemory(MEMORY_DB_PATH)
                # Pre-warm the Ollama model to avoid cold-start latency
                threading.Thread(target=_warm_ollama_model, daemon=True).start()
    return _MEMORY


def _warm_ollama_model():
    """Send a tiny request to keep the model loaded in GPU/RAM."""
    try:
        import ollama  # type: ignore
        ollama.chat(
            model=QA_MODEL,
            messages=[{"role": "user", "content": "hi"}],
            options={"num_predict": 1},
        )
        logger.info(f"Ollama model '{QA_MODEL}' warmed up")
    except Exception:
        pass


def remember_chat_message(session_id: Optional[str], role: str, content: str) -> None:
    try:
        memory = _get_memory()
        memory.add_chat_turn(session_id=session_id, role=role, content=content)
    except Exception as exc:
        logger.warning(f"Failed to remember chat turn: {exc}")


def remember_operation(session_id: Optional[str], operation: Dict[str, Any]) -> None:
    try:
        memory = _get_memory()
        memory.add_operation(session_id=session_id, operation=operation)
    except Exception as exc:
        logger.warning(f"Failed to remember operation: {exc}")


def _react_additional_queries(
    question: str,
    initial_hits: List[SearchResult],
    history: Optional[List[Dict[str, Any]]],
) -> List[str]:
    if not initial_hits:
        return []

    system_prompt = load_prompt_text(
        PROMPT_REACT_QUERY,
        fallback=(
            "You generate retrieval queries for a RAG system. "
            "Return strict JSON: {\"queries\": [\"...\"]}. "
            "Include at most 2 short focused queries. "
            "Do not include explanations."
        ),
    )
    hit_titles = []
    for hit in initial_hits[:4]:
        meta = hit.metadata or {}
        title = meta.get("path") or meta.get("type") or meta.get("source_key") or hit.entry_id
        hit_titles.append(str(title))
    user_payload = {
        "question": question,
        "history": _format_history(history, limit=6),
        "initial_sources": hit_titles,
    }
    content = _ollama_chat(
        model=QA_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        json_mode=True,
        temperature=0.0,
        reasoning=QA_REASONING,
    )
    parsed = _extract_json_block(content or "")
    if not parsed:
        return []
    queries = parsed.get("queries")
    if not isinstance(queries, list):
        return []
    cleaned = []
    for q in queries[:2]:
        q_text = str(q).strip()
        if q_text:
            cleaned.append(q_text)
    return cleaned


def _format_retrieved_context(hits: List[SearchResult]) -> str:
    if not hits:
        return "No retrieved context."

    lines: List[str] = []
    for idx, hit in enumerate(hits, start=1):
        meta = hit.metadata or {}
        source = meta.get("path") or meta.get("type") or meta.get("source_key") or hit.entry_id
        source_text = str(source).replace("\\", "/").lower()
        # Avoid prompt/policy echoing from internal assistant prompt files.
        if source_text.endswith("/core/assistant.py") or "/prompts/" in source_text:
            continue
        snippet = " ".join(hit.text.split())
        if len(snippet) > 550:
            snippet = snippet[:550].rstrip() + "..."
        lines.append(f"[{idx}] source={source} score={hit.score:.3f}")
        lines.append(snippet)
    if not lines:
        return "No retrieved context."
    return "\n".join(lines)


def _fallback_answer(message: str, hits: List[SearchResult], memory_summary: str) -> str:
    if not hits:
        if memory_summary.strip():
            return (
                "I could not query the QA model, but based on current session memory:\n"
                f"{memory_summary}"
            )
        return (
            "I could not retrieve enough context to answer confidently. "
            "Please rephrase or ask a narrower question."
        )

    lines = ["I could not query the QA model. Relevant context I found:"]
    for hit in hits[:3]:
        source = hit.metadata.get("path") or hit.metadata.get("type") or hit.entry_id
        snippet = " ".join(hit.text.split())
        if len(snippet) > 260:
            snippet = snippet[:260].rstrip() + "..."
        lines.append(f"- {source}: {snippet}")
    return "\n".join(lines)


def classify_intent(message: str, history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Public API expected by web_server:
    returns dict with at least {"intent": ...}
    """
    history = history or []
    if not message or not message.strip():
        return {"intent": "general_question", "route": "qa", "confidence": 0.3}

    fast_route = _fast_route_intent(message)
    if fast_route:
        return fast_route

    try:
        head = _head_route(message, history)
    except Exception as exc:
        logger.warning(f"Head routing failed: {exc}")
        fallback = _fallback_intent(message)
        return {
            "intent": fallback.get("intent", "general_question"),
            "route": fallback.get("route", "qa"),
            "confidence": fallback.get("confidence", 0.5),
        }

    route = head.get("route", "implementation")
    if route == "needs_clarification":
        if _looks_like_question(message):
            return {
                "intent": "general_question",
                "route": "qa",
                "confidence": head.get("confidence", 0.5),
                "model": HEAD_MODEL,
            }
        question = head.get("question") or "Could you clarify what you want me to do?"
        return {
            "intent": "needs_clarification",
            "route": "needs_clarification",
            "question": question,
            "options": head.get("options") or [],
            "confidence": head.get("confidence", 0.5),
        }

    if route == "qa":
        fallback = _fallback_intent(message)
        qa_intent = "general_question"
        if fallback.get("intent") == "describe_edits":
            qa_intent = "describe_edits"
        return {
            "intent": qa_intent,
            "route": "qa",
            "confidence": head.get("confidence", 0.7),
            "model": HEAD_MODEL,
        }

    impl = _implementation_intent(message, history)
    intent = impl.get("intent", "edit_request")
    if intent == "needs_clarification":
        return {
            "intent": "needs_clarification",
            "route": "implementation",
            "question": impl.get("question") or "Please clarify the action details.",
            "options": impl.get("options") or [],
            "confidence": head.get("confidence", 0.6),
            "model": IMPLEMENTATION_MODEL,
        }

    if intent not in ACTION_INTENTS:
        intent = "edit_request"

    return {
        "intent": intent,
        "route": "implementation",
        "confidence": head.get("confidence", 0.7),
        "model": IMPLEMENTATION_MODEL,
    }


def _build_qa_context(
    message: str,
    history: Optional[List[Dict[str, Any]]],
    memory_summary: str,
    session_id: Optional[str],
) -> Dict[str, Any]:
    """
    Shared context-building for QA responses (both streaming and non-streaming).
    Returns a dict with: system_prompt, messages, hits, is_brief, temperature, num_predict.
    """
    history = history or []
    capability_query = _is_capability_question(message)
    use_retrieval = _should_use_retrieval(message)
    is_brief = _is_brief_conversation_message(message)

    try:
        memory = _get_memory()
    except Exception:
        memory = None

    # Background refresh for capability queries
    if capability_query and memory:
        threading.Thread(
            target=memory.refresh_system_index,
            kwargs={"force": True},
            daemon=True,
        ).start()

    # --- Retrieval ---
    hits: List[SearchResult] = []
    if use_retrieval and memory:
        hits = memory.store.search(
            query=message,
            top_k=4,
            min_score=0.1,
            session_id=session_id or "default",
        )
        if capability_query:
            capability_hits = memory.store.search(
                query="editbot capabilities tools inputs outputs limitations requirements",
                top_k=6,
                min_score=0.03,
                session_id=session_id or "default",
            )
            merged_cap: Dict[str, SearchResult] = {hit.entry_id: hit for hit in hits}
            for hit in capability_hits:
                existing = merged_cap.get(hit.entry_id)
                if existing is None or hit.score > existing.score:
                    merged_cap[hit.entry_id] = hit
            hits = sorted(merged_cap.values(), key=lambda item: item.score, reverse=True)[:8]

        # ReAct extra queries
        if QA_REACT_ENABLED and len(message.strip()) >= 18 and hits:
            extra_queries = _react_additional_queries(message, hits, history)
            merged: Dict[str, SearchResult] = {hit.entry_id: hit for hit in hits}
            for query in extra_queries:
                for hit in memory.store.search(
                    query=query, top_k=4, min_score=0.1, session_id=session_id or "default"
                ):
                    existing = merged.get(hit.entry_id)
                    if existing is None or hit.score > existing.score:
                        merged[hit.entry_id] = hit
            hits = sorted(merged.values(), key=lambda item: item.score, reverse=True)[:6]

    context_text = _format_retrieved_context(hits) if use_retrieval else ""

    # --- System prompt ---
    if is_brief:
        system_prompt = load_prompt_text(
            PROMPT_QA_BRIEF,
            fallback=(
                "You are EditBot, an AI video editing assistant. "
                "Reply directly and naturally to the user's latest message. "
                "Be friendly, conversational, and helpful."
            ),
        )
    else:
        system_prompt = load_prompt_text(
            PROMPT_QA_STANDARD,
            fallback=(
                "You are EditBot, an AI video editing assistant. "
                "Answer directly using relevant context and recent memory. "
                "Be thorough, helpful, and conversational."
            ),
        )

    if not QA_REASONING:
        system_prompt += " Do not reveal chain-of-thought. Provide final answers only."

    # Inject context and memory into system prompt for better LLM understanding
    context_block = ""
    if context_text:
        context_block += f"\n\n## Retrieved Knowledge\n{context_text}"
    if memory_summary and not is_brief:
        context_block += f"\n\n## Session State\n{memory_summary}"
    if context_block:
        system_prompt += context_block

    # --- Build proper chat-style messages (multi-turn) ---
    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]

    # Add conversation history as proper role-based messages for natural flow
    history_limit = 6 if is_brief else 14
    for item in (history or [])[-history_limit:]:
        role = str(item.get("role", "user")).strip().lower()
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        if role in ("user", "assistant"):
            messages.append({"role": role, "content": content})

    # Add current user message
    messages.append({"role": "user", "content": message})

    return {
        "system_prompt": system_prompt,
        "messages": messages,
        "hits": hits,
        "is_brief": is_brief,
        "temperature": 0.3 if is_brief else 0.5,
        "num_predict": min(QA_MAX_TOKENS, 120) if is_brief else QA_MAX_TOKENS,
    }


def generate_response(
    message: str,
    history: Optional[List[Dict[str, Any]]] = None,
    memory_summary: str = "",
    session_id: Optional[str] = None,
) -> str:
    """Non-streaming QA response."""
    if not message.strip():
        return "Please provide a question."

    try:
        ctx = _build_qa_context(message, history, memory_summary, session_id)
    except Exception as exc:
        logger.warning(f"QA context build failed: {exc}")
        return "I'm having trouble accessing my knowledge base right now. Please try again."

    content = _ollama_chat(
        model=QA_MODEL,
        messages=ctx["messages"],
        json_mode=False,
        temperature=ctx["temperature"],
        reasoning=QA_REASONING,
        num_predict=ctx["num_predict"],
    )
    if content:
        return content.strip()

    return _fallback_answer(message=message, hits=ctx["hits"], memory_summary=memory_summary)


def generate_response_stream(
    message: str,
    history: Optional[List[Dict[str, Any]]] = None,
    memory_summary: str = "",
    session_id: Optional[str] = None,
):
    """
    Streaming QA response. Yields text chunks as they arrive from the LLM.
    This powers the ChatGPT/Claude-like typing effect in the UI.
    """
    if not message.strip():
        yield "Please provide a question."
        return

    try:
        ctx = _build_qa_context(message, history, memory_summary, session_id)
    except Exception as exc:
        logger.warning(f"QA context build failed: {exc}")
        yield "I'm having trouble accessing my knowledge base right now. Please try again."
        return

    yielded_any = False
    for chunk in _ollama_chat_stream(
        model=QA_MODEL,
        messages=ctx["messages"],
        temperature=ctx["temperature"],
        reasoning=QA_REASONING,
        num_predict=ctx["num_predict"],
    ):
        if chunk:
            yielded_any = True
            yield chunk

    if not yielded_any:
        yield _fallback_answer(message=message, hits=ctx["hits"], memory_summary=memory_summary)
