from app.main import parse_highlight_options_from_prompt
from core.schema import CaptionStyle
from tools.captions_tool import CaptionsTool


class _DummyConfigLoader:
    def get_config(self, name):
        if name == "colors":
            return {
                "colors": {
                    "yellow": "00FFFF",
                    "black": "000000",
                    "white": "FFFFFF",
                }
            }
        return {}


def test_no_highlight_prompt_sets_force_disable():
    options = parse_highlight_options_from_prompt(
        "add captions with no highlight",
        _DummyConfigLoader(),
    )
    assert options["enabled"] is False
    assert options["force_disable"] is True


def test_explicit_enabled_false_is_respected():
    tool = CaptionsTool()
    options = tool._normalize_highlight_options(
        {
            "enabled": False,
            "highlight_type": "progressive",
            "highlight_color": "00FFFF",
            "current_word_box": True,
        }
    )
    assert options["enabled"] is False


def test_force_disable_overrides_highlight_fields():
    tool = CaptionsTool()
    options = tool._normalize_highlight_options(
        {
            "enabled": True,
            "highlight_type": "progressive",
            "highlight_color": "00FFFF",
            "force_disable": True,
        }
    )
    assert options["enabled"] is False
    assert options["force_disable"] is True


def test_generate_ass_respects_force_disable(tmp_path):
    tool = CaptionsTool()
    output_path = tmp_path / "captions.ass"
    result = tool.generate_ass_file(
        words=[
            {"word": "hello", "start": 0.0, "end": 0.5},
            {"word": "world", "start": 0.5, "end": 1.0},
        ],
        output_path=str(output_path),
        style=CaptionStyle(),
        highlight_options={
            "enabled": True,
            "highlight_type": "progressive",
            "highlight_color": "00FFFF",
            "force_disable": True,
        },
    )
    assert result["success"] is True
    assert result["highlight_enabled"] is False
    assert result["highlight_events"] == 0
