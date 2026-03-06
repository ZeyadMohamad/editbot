"""
Captions tool for generating ASS subtitle files.
"""
import re
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from core.schema import CaptionStyle
from core.logging import setup_logger
from tools.base_tool import BaseTool, ToolResult, register_tool

logger = setup_logger("captions_tool")

RTL_LANGUAGE_CODES = {"ar", "fa", "ur", "he", "ps"}
ARABIC_CHAR_RE = re.compile(r"[\u0600-\u06FF]")
# Punctuation characters that may need to be flipped for RTL overlay
_PUNCT_CHARS = set(".,!?:;\u060C\u061B\u061F\u2026\u2014\u2013-()[]{}\"'\u00AB\u00BB")


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

    def _normalize_words(self, words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Normalize word payload and sort by start time."""
        normalized: List[Dict[str, Any]] = []

        for idx, word in enumerate(words):
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

            normalized.append({
                "word": token,
                "start": start,
                "end": end,
                "_idx": idx
            })

        normalized.sort(key=lambda w: (w["start"], w["end"], w["_idx"]))
        return normalized
    
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

    def _extract_alpha_hex(self, alpha_value, fallback: str = "66") -> str:
        """Extract 2-digit alpha channel for ASS (&HAA...)."""
        if isinstance(alpha_value, str):
            cleaned = alpha_value.strip().replace("&H", "")
            if len(cleaned) == 2:
                try:
                    int(cleaned, 16)
                    return cleaned.upper()
                except ValueError:
                    return fallback
        if isinstance(alpha_value, int):
            value = max(0, min(255, alpha_value))
            return f"{value:02X}"
        return fallback

    def _to_bool(self, value, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on", "enabled"}:
                return True
            if lowered in {"0", "false", "no", "off", "disabled"}:
                return False
        return default

    def _normalize_highlight_options(self, highlight_options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Normalize highlight configuration used for layered rendering."""
        defaults = {
            "enabled": False,
            "force_disable": False,
            "highlight_type": "word_by_word",  # word_by_word | progressive
            "highlight_color": "00FFFF",       # yellow in ASS BGR
            "progressive_color": "00FFFF",
            "current_word_bold": False,
            "current_word_box": False,
            "box_color": "000000",
            "box_alpha": "66",
            "box_padding": 6,
        }

        incoming = dict(highlight_options or {})

        alias_map = {
            "type": "highlight_type",
            "current_word_color": "highlight_color",
            "progressive_enabled": "highlight_type",
            "word_box": "current_word_box",
            "current_word_box_color": "box_color",
            "current_word_box_alpha": "box_alpha",
        }

        for old_key, new_key in alias_map.items():
            if old_key in incoming and new_key not in incoming:
                incoming[new_key] = incoming[old_key]

        options = dict(defaults)
        options.update(incoming)
        explicit_enabled = "enabled" in incoming
        force_disable = any(
            self._to_bool(incoming.get(key), False)
            for key in ("force_disable", "no_highlight", "disable_highlight", "disable")
        )

        if force_disable:
            options = dict(defaults)
            options["enabled"] = False
            options["force_disable"] = True
            return options

        # Handle legacy bool progressive_enabled => highlight_type
        if "progressive_enabled" in incoming:
            options["highlight_type"] = "progressive" if self._to_bool(incoming.get("progressive_enabled"), False) else "word_by_word"
            options["enabled"] = True

        if str(options.get("highlight_type", "")).lower() in {"progressive", "line_progressive"}:
            options["highlight_type"] = "progressive"
        else:
            options["highlight_type"] = "word_by_word"

        options["enabled"] = self._to_bool(options.get("enabled"), False)
        if not options["enabled"] and highlight_options and not explicit_enabled:
            # If user provided any highlight-specific field, enable highlighting.
            for key in ["highlight_type", "highlight_color", "current_word_bold", "current_word_box", "progressive_color", "box_color"]:
                if key in incoming:
                    options["enabled"] = True
                    break

        options["highlight_color"] = self._extract_color_hex(options.get("highlight_color"))
        options["progressive_color"] = self._extract_color_hex(options.get("progressive_color", options["highlight_color"]))
        options["box_color"] = self._extract_color_hex(options.get("box_color", "000000"))
        options["box_alpha"] = self._extract_alpha_hex(options.get("box_alpha"), "66")
        options["current_word_bold"] = self._to_bool(options.get("current_word_bold"), False)
        options["current_word_box"] = self._to_bool(options.get("current_word_box"), False)

        try:
            options["box_padding"] = max(0, int(options.get("box_padding", 6)))
        except Exception:
            options["box_padding"] = 6

        return options

    def _escape_ass_text(self, text: str) -> str:
        return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")

    def _join_tokens(self, tokens: List[Tuple[str, str]]) -> str:
        """Join ASS tokens while avoiding spaces before punctuation."""
        if not tokens:
            return ""

        no_space_before = {
            ".",
            ",",
            "!",
            "?",
            ":",
            ";",
            "\u060C",  # Arabic comma
            "\u061B",  # Arabic semicolon
            "\u061F",  # Arabic question mark
            "\u2026",  # ellipsis
            ")",
            "]",
            "}",
            "\u00BB"   # »
        }
        no_space_after = {"(", "[", "{", "\u00AB"}  # «

        parts: List[str] = []
        for idx, (styled_text, plain_text) in enumerate(tokens):
            if idx == 0:
                parts.append(styled_text)
                continue

            prev_plain = tokens[idx - 1][1]
            if plain_text in no_space_before or prev_plain in no_space_after:
                parts.append(styled_text)
            else:
                parts.append(f" {styled_text}")

        return "".join(parts)

    def _get_center_alignment(self, position: str) -> int:
        """Center captions horizontally."""
        pos = (position or "bottom").lower()
        if pos == "top":
            return 8
        if pos == "middle":
            return 5
        return 2

    @staticmethod
    def _flip_punctuation(word: str) -> str:
        """Move trailing punctuation to the front and leading punctuation
        to the back.  Used in the RTL overlay layer so that punctuation
        appears on the correct side when the word order is reversed."""
        if not word:
            return word
        # Strip trailing punctuation
        trailing = []
        i = len(word) - 1
        while i >= 0 and word[i] in _PUNCT_CHARS:
            trailing.append(word[i])
            i -= 1
        # Strip leading punctuation
        leading = []
        j = 0
        core_start = i + 1  # will be overwritten
        while j <= i and word[j] in _PUNCT_CHARS:
            leading.append(word[j])
            j += 1
        core = word[j:i + 1]
        # Flip: trailing → front, leading → back
        return ''.join(reversed(trailing)) + core + ''.join(reversed(leading))

    def _contains_arabic(self, text: str) -> bool:
        return bool(ARABIC_CHAR_RE.search(text or ""))

    def _should_force_rtl(self, line_words: List[Dict[str, Any]], detected_language: Optional[str]) -> bool:
        """
        Force RTL ordering for Arabic/RTL content so first spoken word appears
        on the right and reveal progresses toward the left.
        """
        if detected_language and str(detected_language).lower() in RTL_LANGUAGE_CODES:
            return True
        return any(self._contains_arabic(str(w.get("word", ""))) for w in line_words)

    def _apply_rtl_embedding(self, text: str, force_rtl: bool) -> str:
        """
        Wrap text in RTL embedding marks when needed.
        U+202B (RLE) ... U+202C (PDF)
        """
        if not force_rtl:
            return text
        return f"\u202B{text}\u202C"

    def _build_overlay_text(
        self,
        line_words: List[Dict[str, Any]],
        active_index: int,
        highlight_options: Dict[str, Any],
        alignment: int,
        force_rtl: bool,
        style_outline_color: str = "000000",
        style_outline_width: float = 3,
        style_shadow: float = 2,
    ) -> str:
        """
        Build the overlay dialogue text using inline override tags.

        For RTL text the word order in the source is **reversed** so that
        the LTR run-layout used by ASS renderers produces a visual result
        that matches the RTL base caption (first spoken word on the right,
        last spoken word on the left).  The active_index is remapped
        accordingly and progressive "past" logic is inverted.
        """
        progressive = highlight_options.get("highlight_type") == "progressive"
        use_box = highlight_options.get("current_word_box", False)

        highlight_color = highlight_options.get("highlight_color", "00FFFF")
        progressive_color = highlight_options.get("progressive_color", highlight_color)
        box_color = highlight_options.get("box_color", "000000")
        box_alpha = highlight_options.get("box_alpha", "66")
        box_padding = highlight_options.get("box_padding", 6)
        bold = highlight_options.get("current_word_bold", False)

        # --- reusable tag fragments ----------------------------------------
        hidden_tag = "\\1a&HFF&\\2a&HFF&\\3a&HFF&\\4a&HFF&"

        bold_tag = "\\b1" if bold else ""
        if use_box:
            current_tag = (
                f"\\1a&H00&\\2a&H00&\\3a&H{box_alpha}&\\4a&H{box_alpha}&"
                f"\\1c&H{highlight_color}&\\3c&H{box_color}&\\4c&H{box_color}&"
                f"\\bord{box_padding}\\shad0"
                f"{bold_tag}"
            )
        else:
            current_tag = (
                f"\\1a&H00&\\2a&H00&\\3a&H00&\\4a&H00&"
                f"\\1c&H{highlight_color}&\\3c&H{style_outline_color}&"
                f"\\bord{style_outline_width}\\shad{style_shadow}"
                f"{bold_tag}"
            )

        past_tag = (
            f"\\1a&H00&\\2a&H00&\\3a&H00&\\4a&H00&"
            f"\\1c&H{progressive_color}&\\3c&H{style_outline_color}&"
            f"\\bord{style_outline_width}\\shad{style_shadow}"
        )

        # --- RTL: reverse source order so LTR run-layout matches visual RTL -
        if force_rtl:
            render_words = list(reversed(line_words))
            mapped_active = len(line_words) - 1 - active_index
        else:
            render_words = line_words
            mapped_active = active_index

        # --- build token list -----------------------------------------------
        tokens: List[Tuple[str, str]] = []
        for src_idx, word in enumerate(render_words):
            raw_word = word["word"]
            if force_rtl:
                raw_word = self._flip_punctuation(raw_word)
            token = self._escape_ass_text(raw_word)

            if src_idx == mapped_active:
                tag = current_tag
            elif progressive and force_rtl and src_idx > mapped_active:
                # In reversed order, "past" words (spoken before active)
                # are at higher indices
                tag = past_tag
            elif progressive and not force_rtl and src_idx < mapped_active:
                tag = past_tag
            else:
                tag = hidden_tag

            tokens.append((f"{{{tag}}}{token}", raw_word))

        joined = self._join_tokens(tokens)
        # Do NOT apply RTL embedding on the overlay – the reversed source
        # order already produces the correct visual layout under LTR.
        # Applying RLE would flip it back to the wrong order.
        return f"{{\\an{alignment}}}{joined}"
    
    def generate_ass_file(
        self,
        words: List[Dict[str, Any]],
        output_path: str,
        style: CaptionStyle,
        video_width: int = 1920,
        video_height: int = 1080,
        max_words_per_line: int = 6,
        highlight_options: Optional[Dict[str, Any]] = None,
        detected_language: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate ASS subtitle file with two layers:
        - Layer 0: base caption sentence
        - Layer 1: highlighting overlay (word-by-word or progressive)
        """
        self.logger.info(f"Generating ASS file with {len(words)} words")
        
        try:
            if not words:
                return {"success": False, "error": "No words provided"}
            
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

            normalized_words = self._normalize_words(words)
            if not normalized_words:
                return {"success": False, "error": "No valid words provided"}

            lines = self._group_words_into_lines(normalized_words, max_words_per_line)
            self.logger.info(f"Grouped into {len(lines)} caption lines")

            highlight = self._normalize_highlight_options(highlight_options)

            # Colors
            primary_color = self._extract_color_hex(style.primary_color)
            outline_color = self._extract_color_hex(style.outline_color)
            highlight_color = self._extract_color_hex(highlight.get("highlight_color", "00FFFF"))
            progressive_color = self._extract_color_hex(highlight.get("progressive_color", highlight_color))
            box_color = self._extract_color_hex(highlight.get("box_color", "000000"))
            box_alpha = self._extract_alpha_hex(highlight.get("box_alpha", "66"))

            # Force right-side anchoring for both layers
            alignment = self._get_center_alignment(style.position)
            margin_v = 50 if style.position == "bottom" else (video_height // 2 if style.position == "middle" else 50)

            bold_default = -1 if style.bold else 0
            bold_highlight = -1 if (style.bold or highlight.get("current_word_bold", False)) else 0

            total_highlight_events = 0
            
            with open(output_path, 'w', encoding='utf-8') as f:
                # Write header
                f.write(f"""[Script Info]
Title: Generated Subtitles
ScriptType: v4.00+
WrapStyle: 2
PlayResX: {video_width}
PlayResY: {video_height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
""")
                
                # Layer 0 style (base captions)
                f.write(
                    f"Style: Default,{style.font},{style.font_size},&H00{primary_color},&H00{primary_color},&H00{outline_color},&H00000000,{bold_default},0,0,0,100,100,0,0,1,{style.outline_width},{style.shadow},{alignment},30,30,{margin_v},1\n"
                )

                # Layer 1 styles (highlight overlay)
                if highlight.get("enabled", False):
                    f.write(
                        f"Style: HiddenOverlay,{style.font},{style.font_size},&HFF{primary_color},&HFF{primary_color},&HFF{outline_color},&HFF000000,{bold_default},0,0,0,100,100,0,0,1,{style.outline_width},{style.shadow},{alignment},30,30,{margin_v},1\n"
                    )
                    f.write(
                        f"Style: PastHighlight,{style.font},{style.font_size},&H00{progressive_color},&H00{progressive_color},&H00{outline_color},&H00000000,{bold_default},0,0,0,100,100,0,0,1,{style.outline_width},{style.shadow},{alignment},30,30,{margin_v},1\n"
                    )
                    f.write(
                        f"Style: CurrentHighlight,{style.font},{style.font_size},&H00{highlight_color},&H00{highlight_color},&H00{outline_color},&H00000000,{bold_highlight},0,0,0,100,100,0,0,1,{style.outline_width},{style.shadow},{alignment},30,30,{margin_v},1\n"
                    )
                    f.write(
                        f"Style: CurrentBox,{style.font},{style.font_size},&H00{highlight_color},&H00{highlight_color},&H{box_alpha}{box_color},&H{box_alpha}{box_color},{bold_highlight},0,0,0,100,100,0,0,3,{highlight.get('box_padding', 6)},0,{alignment},30,30,{margin_v},1\n"
                    )
                
                f.write("\n[Events]\n")
                f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
                
                # Layer 0 + Layer 1 events
                for line_words in lines:
                    line_start = line_words[0]["start"]
                    line_end = line_words[-1]["end"]
                    if line_end <= line_start:
                        line_end = line_start + 0.2

                    force_rtl = self._should_force_rtl(line_words, detected_language)

                    start_ts = self._format_timestamp(line_start)
                    end_ts = self._format_timestamp(line_end)

                    base_tokens = [
                        (self._escape_ass_text(w["word"]), w["word"])
                        for w in line_words
                    ]
                    base_text = self._join_tokens(base_tokens)
                    base_text = self._apply_rtl_embedding(base_text, force_rtl)
                    f.write(f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{{\\an{alignment}}}{base_text}\n")

                    if not highlight.get("enabled", False):
                        continue

                    for idx, word in enumerate(line_words):
                        word_start = float(word.get("start", line_start))
                        word_end = float(word.get("end", word_start))
                        if word_end <= word_start:
                            if idx < len(line_words) - 1:
                                next_start = float(line_words[idx + 1].get("start", word_start + 0.08))
                                word_end = max(word_start + 0.04, next_start)
                            else:
                                word_end = word_start + 0.08

                        overlay_text = self._build_overlay_text(
                            line_words=line_words,
                            active_index=idx,
                            highlight_options=highlight,
                            alignment=alignment,
                            force_rtl=force_rtl,
                            style_outline_color=outline_color,
                            style_outline_width=style.outline_width,
                            style_shadow=style.shadow,
                        )
                        f.write(
                            f"Dialogue: 1,{self._format_timestamp(word_start)},{self._format_timestamp(word_end)},Default,,0,0,0,,{overlay_text}\n"
                        )
                        total_highlight_events += 1
            
            self.logger.info(f"ASS file generated: {output_path}")
            return {
                "success": True,
                "output_path": output_path,
                "subtitle_file": output_path,
                "total_lines": len(lines),
                "total_words": len(normalized_words),
                "highlight_enabled": highlight.get("enabled", False),
                "highlight_type": highlight.get("highlight_type"),
                "highlight_events": total_highlight_events,
                "right_aligned_layers": True
            }
            
        except Exception as e:
            self.logger.error(f"Error: {str(e)}")
            return {"success": False, "error": str(e)}
