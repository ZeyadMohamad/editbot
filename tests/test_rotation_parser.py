from app.main import parse_rotation_from_prompt, rotation_mentioned
from core.assistant import classify_intent
from tools.rotate_tool import parse_rotation_cw_degrees


def test_parse_rotation_degrees_clockwise():
    assert parse_rotation_cw_degrees("rotate 45 degrees clockwise") == 45.0


def test_parse_rotation_degrees_counterclockwise():
    assert parse_rotation_cw_degrees("rotate 30 degrees counterclockwise") == 330.0


def test_parse_rotation_left_times_to_clockwise():
    assert parse_rotation_cw_degrees("rotate left 2 times") == 180.0
    assert parse_rotation_cw_degrees("rotate left once") == 270.0


def test_parse_rotation_right_times():
    assert parse_rotation_cw_degrees("rotate right 3 times") == 270.0


def test_parse_rotation_from_prompt_helper():
    prompt = "Please rotate right twice and save the output."
    assert rotation_mentioned(prompt) is True
    assert parse_rotation_from_prompt(prompt) == 180.0


def test_rotate_prompt_routes_to_implementation():
    result = classify_intent("rotate this video left 2 times and save it", [])
    assert result["intent"] == "edit_request"
    assert result["route"] == "implementation"
