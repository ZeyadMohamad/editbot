# EditBot 🎬

**AI-Powered Video Editing Assistant** - Automatically add captions with word-level highlighting to your videos using local AI models.

## ✨ Features

- 🎤 **Automatic Transcription** - Speech-to-text using Whisper (supports 20+ languages)
- 📝 **Word-Level Captions** - Each word highlighted as it's spoken (karaoke style)
- 🎨 **Customizable Styles** - Fonts, colors, positions, highlight effects
- ✂️ **Silence Cutter** - Remove dead air and filler words (Arabic/English/code-switched)
- 🤖 **AI-Powered** - Uses local Llama 3 via Ollama for understanding prompts
- 🔧 **Extensible** - Easy to add new tools and features

## 🚀 Quick Start

### Prerequisites

1. **FFmpeg** - Video processing
   ```powershell
   winget install ffmpeg
   ```

2. **Ollama** - Local LLM
   ```powershell
   winget install Ollama.Ollama
   ollama pull llama3
   ```

3. **Python 3.10+**

### Installation

```powershell
cd "D:\Video Editing Project\editbot"

# Create virtual environment
python -m venv .venv

# Activate it
.\.venv\Scripts\Activate.ps1

# Run installation script
.\install.ps1

# Test installation
python test_system.py
```

### Usage

**Interactive Mode:**
```powershell
python -m app.main --interactive
```

**Command Line:**
```powershell
python -m app.main -v "path/to/video.mp4" -p "Add yellow captions with TikTok style"
```

**Examples:**
```powershell
# Basic captions
python -m app.main -v video.mp4 -p "Add captions to this video"

# Custom styling
python -m app.main -v video.mp4 -p "Add white text with red highlight, font size 48, position bottom"

# With output directory
python -m app.main -v video.mp4 -p "Add karaoke style captions" -o ./output

# Remove silence + filler words, then caption
python -m app.main -v video.mp4 -p "Remove silence and filler words, then add captions"
```

---

## 📁 Project Structure

```
editbot/
├── app/                    # Application entry point
│   ├── __init__.py
│   └── main.py            # CLI interface
│
├── core/                   # Core business logic
│   ├── __init__.py
│   ├── config_loader.py   # Smart config loading system
│   ├── logging.py         # Logging configuration
│   ├── orchestrator.py    # Job execution engine
│   ├── planner.py         # AI-powered plan generation
│   ├── schema.py          # Pydantic data models
│   └── state.py           # State management
│
├── tools/                  # Video editing tools
│   ├── __init__.py        # Tool registration
│   ├── base_tool.py       # Base class for all tools
│   ├── ffmpeg_tool.py     # FFmpeg operations
│   ├── whisperx_tool.py   # Speech transcription
│   └── captions_tool.py   # ASS subtitle generation
│
├── configs/               # Configuration files (JSON)
│   ├── fonts.json         # Available fonts
│   ├── colors.json        # Color palette (BGR format)
│   ├── highlight_styles.json  # Caption highlight effects
│   ├── text_styles.json   # Bold, italic, etc.
│   ├── positions.json     # Screen positions
│   ├── ffmpeg_settings.json   # Encoding settings
│   ├── supported_formats.json # File format support
│   ├── supported_languages.json # Transcription languages
│   └── silence_cutter.json # Silence cut defaults & filler words
│
├── registry/              # Tool & config registry
│   ├── tools.json         # Tool definitions
│   └── config_map.json    # Keyword-to-config mapping
│
├── prompts/               # External prompt templates
│   ├── system_prompt.txt  # Main LLM instructions
│   ├── caption_instructions.txt
│   ├── plan_schema.txt
│   └── user_examples.md
│
├── presets/               # User presets
│   └── default.json
│
├── workspace/             # Output directory (generated)
├── logs/                  # Log files (generated)
│
├── test_system.py         # System verification
├── install.ps1            # Installation script
├── requirements.txt       # Python dependencies
└── README.md              # This file
```

---

## 📄 File Descriptions

### Core Modules

| File | Description |
|------|-------------|
| `core/schema.py` | Pydantic models for `Job`, `Plan`, `CaptionStyle`, `VideoInfo` - defines data structures |
| `core/planner.py` | Uses Llama 3 to convert user prompts into execution plans |
| `core/orchestrator.py` | Executes plans by running tools in dependency order with retry logic |
| `core/config_loader.py` | Smart config loading - only loads configs relevant to user's request |
| `core/state.py` | Manages workspace, temporary files, and execution state |
| `core/logging.py` | Centralized logging configuration |

### Tools

| File | Description |
|------|-------------|
| `tools/base_tool.py` | Abstract base class that all tools extend. Provides `@register_tool` decorator, `ToolResult` class, and `ToolRegistry` singleton |
| `tools/ffmpeg_tool.py` | Video/audio operations: extract audio, get video info, render subtitles |
| `tools/whisperx_tool.py` | Speech transcription using Whisper with word-level timestamps |
| `tools/captions_tool.py` | Generates ASS subtitle files with karaoke-style word highlighting |
| `tools/silence_cutter_tool.py` | Detects silence and filler words, outputs cut list and trimmed media |

### Configuration Files

| File | Description |
|------|-------------|
| `configs/fonts.json` | List of available fonts with variants (Arial, Montserrat, etc.) |
| `configs/colors.json` | Color palette in ASS BGR hex format (white=FFFFFF, red=0000FF) |
| `configs/highlight_styles.json` | Caption highlight effects (color_change, karaoke_fill, scale_pop, glow, bounce) |
| `configs/text_styles.json` | Text formatting options with ASS tags |
| `configs/positions.json` | Screen positions with ASS alignment values |
| `configs/ffmpeg_settings.json` | Video encoding presets and codec settings |
| `configs/supported_formats.json` | Supported video/audio/subtitle file extensions |
| `configs/supported_languages.json` | Languages supported by Whisper transcription |
| `configs/silence_cutter.json` | Defaults and filler word lists for silence cutting |

### Registry Files

| File | Description |
|------|-------------|
| `registry/tools.json` | Complete registry of all tools with their inputs, outputs, dependencies, and required config files |
| `registry/config_map.json` | Maps keywords to config files - enables smart config loading based on user prompt |

### Prompt Templates

| File | Description |
|------|-------------|
| `prompts/system_prompt.txt` | Main instructions for the LLM planner |
| `prompts/caption_instructions.txt` | Specific instructions for caption generation |
| `prompts/plan_schema.txt` | JSON schema documentation for execution plans |

---

## 🎨 Caption Styling Options

### Colors (BGR Format for ASS)
```
white, black, red, green, blue, yellow, cyan, magenta, 
orange, purple, pink, lime, coral, gold, navy, silver
```

### Positions
```
bottom, middle, top, bottom-left, bottom-right, 
top-left, top-right, center
```

### Highlight Styles
- `color_change` - Word changes color when spoken
- `karaoke_fill` - Word fills from left to right
- `scale_pop` - Word scales up briefly
- `glow` - Word gets glow effect
- `bounce` - Word bounces

### Fonts
```
Arial, Calibri, Comic Sans MS, Courier New, Georgia, Impact,
Montserrat, Open Sans, Roboto, Times New Roman, Trebuchet MS, Verdana
```

---

## 🔧 Architecture

### How It Works

1. **User provides prompt**: "Add yellow captions with red highlight at bottom"

2. **Config Loader** analyzes prompt keywords:
   - "yellow", "red" → load `colors.json`
   - "captions" → load `fonts.json`, `positions.json`
   - "highlight" → load `highlight_styles.json`

3. **Planner** (Llama 3) creates execution plan:
   ```json
   {
     "jobs": [
       {"tool": "ffmpeg", "operation": "extract_audio"},
       {"tool": "whisperx", "operation": "transcribe"},
       {"tool": "captions", "operation": "generate_ass"},
       {"tool": "ffmpeg", "operation": "render_subtitles"}
     ]
   }
   ```

4. **Orchestrator** executes jobs in order, passing outputs between them

5. **Output**: Captioned video saved to workspace

### Tool System (BaseTool Pattern)

All tools inherit from `BaseTool`:

```python
@register_tool
class MyNewTool(BaseTool):
    tool_id = "my_tool"
    tool_name = "My Tool"
    description = "Description"
    category = "category"
    
    def execute(self, operation, **kwargs):
        # Implementation
        pass
```

The `@register_tool` decorator automatically registers the tool with the `ToolRegistry`.

---

## 🌐 Supported Languages

Whisper supports 20+ languages for transcription:

| Language | Code | Language | Code |
|----------|------|----------|------|
| English | en | Japanese | ja |
| Spanish | es | Korean | ko |
| French | fr | Chinese | zh |
| German | de | Arabic | ar |
| Italian | it | Hindi | hi |
| Portuguese | pt | Dutch | nl |
| Russian | ru | Polish | pl |
| Turkish | tr | Vietnamese | vi |
| Thai | th | Indonesian | id |
| Malay | ms | Filipino | fil |

---

## 💻 System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| OS | Windows 10 | Windows 11 |
| Python | 3.10 | 3.12 |
| RAM | 8 GB | 16 GB |
| GPU | GTX 1060 4GB | RTX 3060+ |
| Storage | 5 GB | 10 GB |

### GPU Memory Guide

| Whisper Model | VRAM Required | Speed |
|---------------|---------------|-------|
| tiny | 1 GB | Fastest |
| base | 2 GB | Fast |
| small | 3 GB | Good |
| medium | 5 GB | Slow |
| large | 10 GB | Slowest |

For RTX 3050 (4GB), use `base` or `small` model.

---

## 🐛 Troubleshooting

### "CUDA out of memory"
- Edit `tools/whisperx_tool.py` and change `model_size="base"` to `"tiny"`

### "FFmpeg not found"
- Install FFmpeg: `winget install ffmpeg`
- Or download from: https://ffmpeg.org/download.html

### "Ollama connection failed"
- Start Ollama: `ollama serve`
- Pull model: `ollama pull llama3`

### "No audio in output"
- Video might not have audio track
- Check with: `ffprobe -i video.mp4`

---

## 📜 License

MIT License - Feel free to use and modify!

---

## 🤝 Adding New Features

### Adding a New Tool

1. Create `tools/my_tool.py`:
```python
from tools.base_tool import BaseTool, ToolResult, register_tool

@register_tool
class MyTool(BaseTool):
    tool_id = "my_tool"
    tool_name = "My Tool"
    description = "Description"
    category = "category"
    
    def execute(self, operation, **kwargs):
        # Implementation
        pass
```

2. Import in `tools/__init__.py`:
```python
from tools.my_tool import MyTool
```

3. Add to `registry/tools.json`

### Adding a New Config

1. Create `configs/my_config.json`

2. Add keyword mapping to `registry/config_map.json`:
```json
{
  "keyword_mappings": {
    "my_keyword": ["my_config"]
  }
}
```

---

Built with ❤️ using Python, FFmpeg, Whisper, and Ollama
