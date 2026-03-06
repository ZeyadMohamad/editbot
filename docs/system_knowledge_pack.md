# EditBot System Knowledge Pack

This document is generated from local project files and indexed into the vector database.
Use it as authoritative context for capability and architecture questions.

## Core Purpose
- EditBot automates video-editing workflows driven by natural-language prompts.
- It supports captioning, transcription, silence/filler cutting, transitions, stock footage composition, and media rotation.
- It exposes a FastAPI web UI and a CLI entrypoint.

## User Interfaces
- Web app: `/`
- Chat endpoint: `/api/chat`
- Processing endpoint: `/api/process`
- Upload endpoints: `/api/upload`, `/api/uploads`, `/api/session/cleanup`
- Position helper UI: `/position-helper`
- Dynamic uploaded-video streaming endpoint: `/api/video/{video_id}`

## LLM Routing Architecture
- Head model (`EDITBOT_HEAD_MODEL`): route user message to QA vs implementation
- QA model (`EDITBOT_QA_MODEL`): answer using retrieval over vector memory (RAG + ReAct-style extra queries)
- Implementation model (`EDITBOT_IMPLEMENTATION_MODEL`): classify executable intent
- Clarification model defaults to head/QA env chain in `core/clarifier.py`
- Reasoning flags are env-controlled per model and default to off.

## Tool Categories
- `audio`: Audio processing tools
- `video`: Video processing tools
- `transcription`: Speech-to-text tools
- `captions`: Subtitle and caption tools
- `utility`: Utility and helper tools

## Registered Tools
### Tool: align_words
- Name: Align Words
- Description: Get word-level timestamps from transcription
- Module/Method: `tools.whisperx_tool.align_words`
- Category: `transcription`
- Depends on: transcribe
- Config files: none
- Inputs:
  - `transcription_result` (object, required): Result from transcribe tool
- Outputs:
  - `words` (array): Words with start/end timestamps

### Tool: apply_transitions
- Name: Apply Transitions
- Description: Combine multiple clips using FFmpeg xfade transitions
- Module/Method: `tools.ffmpeg_tool.apply_transitions`
- Category: `video`
- Depends on: none
- Config files: transitions.json, ffmpeg_settings.json, supported_formats.json
- Inputs:
  - `clips` (array, optional): Ordered list of clips. Each item can be a path string or {path,start_time,end_time,duration}. Required if segments is not provided.
  - `segments` (array, optional): Optional list of segments for a single source clip. Each item can be {start_time,end_time,duration} with optional path.
  - `source_path` (file, optional): Default clip path to use when segments or clip items omit path.
  - `transitions` (array, optional): Optional list of transitions between clips. Each item can be a string or {name/code,duration}
  - `transition_duration` (float, optional, default=1.0): Default transition duration (seconds) when not specified per transition
  - `output_path` (file, required): Output video path
  - `codec` (string, optional, default=libx264):
  - `preset` (string, optional, default=medium):
  - `crf` (int, optional, default=23):
- Outputs:
  - `video_file` (file): Output video with transitions

### Tool: extract_audio
- Name: Extract Audio
- Description: Extract audio track from video file for processing
- Module/Method: `tools.ffmpeg_tool.extract_audio`
- Category: `audio`
- Depends on: none
- Config files: ffmpeg_settings.json, supported_formats.json
- Inputs:
  - `video_path` (file, required): Path to input video
  - `output_path` (file, required): Path for output audio
  - `sample_rate` (int, optional, default=16000):
  - `audio_format` (string, optional, default=wav):
- Outputs:
  - `audio_file` (file): Extracted audio file

### Tool: generate_captions
- Name: Generate Captions
- Description: Generate ASS subtitle file with styled captions
- Module/Method: `tools.captions_tool.generate_ass_file`
- Category: `captions`
- Depends on: align_words
- Config files: fonts.json, colors.json, highlight_styles.json, text_styles.json, positions.json
- Inputs:
  - `words` (array, required): Words with timestamps
  - `output_path` (file, required): Output ASS file path
  - `style` (object, optional): Caption style configuration
  - `highlight_options` (object, optional): Optional layered highlight configuration (enabled, highlight_type=word_by_word|progressive, colors, bold, box)
  - `detected_language` (string, optional): Detected language code, optional
- Outputs:
  - `ass_file` (file): Generated ASS subtitle file

### Tool: get_video_info
- Name: Get Video Info
- Description: Get video metadata like duration, resolution, fps
- Module/Method: `tools.ffmpeg_tool.get_video_info`
- Category: `utility`
- Depends on: none
- Config files: supported_formats.json
- Inputs:
  - `video_path` (file, required): Path to video file
- Outputs:
  - `duration` (float): Video duration in seconds
  - `width` (int): Video width
  - `height` (int): Video height
  - `fps` (float): Frames per second

### Tool: render_subtitles
- Name: Render Subtitles
- Description: Burn subtitles onto video
- Module/Method: `tools.ffmpeg_tool.render_subtitles`
- Category: `video`
- Depends on: generate_captions
- Config files: ffmpeg_settings.json, supported_formats.json
- Inputs:
  - `video_path` (file, required): Input video path
  - `subtitle_path` (file, required): ASS subtitle file path
  - `output_path` (file, required): Output video path
  - `codec` (string, optional, default=libx264):
  - `preset` (string, optional, default=medium):
  - `crf` (int, optional, default=23):
- Outputs:
  - `video_file` (file): Video with burned-in subtitles

### Tool: rotate_media
- Name: Rotate Media
- Description: Rotate a single video or image by clockwise degrees and save output
- Module/Method: `tools.rotate_tool.rotate_media`
- Category: `video`
- Depends on: none
- Config files: supported_formats.json, ffmpeg_settings.json
- Inputs:
  - `input_path` (file, required): Path to input video or image
  - `output_path` (file, required): Path for rotated output media
  - `rotation_cw_deg` (any, required): Clockwise rotation in degrees, or text like 'rotate left 2 times'
  - `codec` (string, optional, default=libx264):
  - `preset` (string, optional, default=medium):
  - `crf` (int, optional, default=23):
- Outputs:
  - `media_file` (file): Rotated media output

### Tool: silence_cutter
- Name: Silence Cutter
- Description: Detect silence and filler words, produce cut list and optionally trimmed media
- Module/Method: `tools.silence_cutter_tool.cut_silence`
- Category: `audio`
- Depends on: extract_audio
- Config files: silence_cutter.json, supported_formats.json, supported_languages.json
- Inputs:
  - `audio_path` (file, required): Path to extracted WAV audio
  - `video_path` (file, optional): Optional input video path
  - `output_path` (file, optional): Output trimmed video path
  - `output_audio_path` (file, optional): Output trimmed audio path
  - `threshold_db` (float, optional, default=-35.0):
  - `min_silence_duration` (float, optional, default=0.3):
  - `padding` (float, optional, default=0.05):
  - `chunk_ms` (int, optional, default=30):
  - `filler_detection` (bool, optional, default=True):
  - `filler_model_size` (string, optional, default=small):
  - `filler_language` (string, optional, default=auto):
  - `filler_confidence` (float, optional, default=0.5):
  - `filler_aggressive` (bool, optional, default=False):
  - `manual_cut_segments` (array, optional): List of manual cut ranges {start,end} in seconds. If provided, overrides auto detection.
- Outputs:
  - `cut_list_path` (file): JSON cut list
  - `output_video_path` (file): Trimmed video
  - `output_audio_path` (file): Trimmed audio

### Tool: stock_footage
- Name: Stock Footage
- Description: Overlay or insert stock videos/images into a base video
- Module/Method: `tools.stock_footage_tool.apply_stock_footage`
- Category: `video`
- Depends on: none
- Config files: supported_formats.json
- Inputs:
  - `video_path` (file, required): Path to base video
  - `stock_items` (array, required): List of stock items with path, mode, and timing
  - `output_path` (file, required): Output video path
  - `codec` (string, optional, default=libx264):
  - `preset` (string, optional, default=medium):
  - `crf` (int, optional, default=23):
- Outputs:
  - `video_file` (file): Video with stock footage

### Tool: transcribe
- Name: Transcribe Audio
- Description: Transcribe audio to text with timestamps using Whisper
- Module/Method: `tools.whisperx_tool.transcribe`
- Category: `transcription`
- Depends on: extract_audio
- Config files: supported_languages.json
- Inputs:
  - `audio_path` (file, required): Path to audio file
  - `language` (string, optional, default=auto): Language code or 'auto'
- Outputs:
  - `text` (string): Full transcribed text
  - `language` (string): Detected language
  - `segments` (array): Transcript segments with timestamps

## Pipelines (registry/tools.json)
- `caption_pipeline`: Full pipeline for adding captions to video
  - Steps: extract_audio, transcribe, align_words, generate_captions, render_subtitles
- `silence_cut_pipeline`: Remove silence and filler words from a video
  - Steps: extract_audio, silence_cutter

## Formats And Language Support
- Supported input video extensions: .mp4, .mkv, .avi, .mov, .wmv, .flv, .webm, .m4v, .mpeg, .mpg, .3gp, .ts, .mts
- Supported input audio extensions: .mp3, .wav, .aac, .flac, .ogg, .m4a, .wma
- Supported input image extensions: .jpg, .jpeg, .png, .bmp, .gif, .webp, .tiff
- Supported subtitle output extensions: .srt, .ass, .vtt

- Supported language codes (from config): ar, de, en, es, fr, hi, id, it, ja, ko, nl, pl, pt, ru, sv, th, tr, uk, vi, zh

## Practical Limits And Requirements
- FFmpeg must be installed and available on PATH.
- Whisper transcription quality/speed depends on selected model size and GPU/CPU availability.
- `apply_transitions` requires at least two clips or two segments.
- Stock overlay requires `start_time`; image overlays require explicit duration or end_time.
- Stock insert requires `start_time` and a resolvable stock duration.
- Media rotation accepts clockwise degrees or natural-language left/right turns.
- Silence cutter requires a valid WAV audio input for wave-based analysis.
- Manual cut segments override automatic silence/filler detection when provided.
- Caption generation expects word-level timestamps; no words means no ASS output.
- ASS subtitle burn requires valid source video and subtitle path.

## Silence Cutter Defaults (from config)
- `threshold_db`: -35.0
- `min_silence_duration`: 0.3
- `padding`: 0.05
- `chunk_ms`: 30
- `filler_detection`: True
- `filler_model_size`: small
- `filler_language`: auto
- `filler_confidence`: 0.5
- `filler_aggressive`: False
- `filler_engine`: whisper

## Config Loader Behavior
- ConfigLoader maps prompt keywords/intents to config files using `registry/config_map.json`.
- Keyword-mapped config files: colors.json, ffmpeg_settings.json, fonts.json, highlight_styles.json, positions.json, silence_cutter.json, supported_formats.json, supported_languages.json, text_styles.json, transitions.json
- Intent-to-config mapping:
  - `add_captions` -> fonts.json, colors.json, highlight_styles.json, text_styles.json, positions.json, supported_languages.json
  - `add_transitions` -> transitions.json, ffmpeg_settings.json, supported_formats.json
  - `export_video` -> ffmpeg_settings.json, supported_formats.json
  - `remove_silence` -> silence_cutter.json, supported_formats.json, supported_languages.json
  - `style_captions` -> fonts.json, colors.json, highlight_styles.json, text_styles.json, positions.json
  - `transcribe` -> supported_languages.json

## Output Artifacts
- Common generated artifacts include:
  - extracted audio WAV files
  - transcript JSON files
  - ASS/SRT/VTT subtitle files
  - cut list JSON files
  - final processed video files

## Position Helper
- The Position Helper page shows a dynamic uploaded video and live cursor coordinates.
- It returns both pixel and percentage coordinates for overlay placement.
- It is meant for stock footage/image overlay positioning workflows.
