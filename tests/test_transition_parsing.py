from app.main import _count_steps, parse_transition_from_prompt
from core.clarifier import extract_transition_duration


class _DummyConfigLoader:
    def get_config(self, name):
        if name == "transitions":
            return {
                "transitions": [
                    {"name": "Cross Dissolve", "code": "fade"},
                    {"name": "Dip to Black", "code": "fadeblack"},
                ]
            }
        return {}


def test_parse_transition_duration_phrase():
    prompt = "Apply a cross dissolve transition at 0.000s with a duration of 0.5s."
    transition = parse_transition_from_prompt(prompt, _DummyConfigLoader())
    assert transition is not None
    assert transition["name"] == "Cross Dissolve"
    assert transition["duration"] == 0.5


def test_count_steps_includes_cut_transition_pass():
    assert _count_steps(["cut"], use_captions=False, apply_cut_transitions=True) == 3


def test_parse_transition_duration_range_phrase():
    prompt = "Then apply a cross dissolve transition from duration 0.000s to 0.500s."
    transition = parse_transition_from_prompt(prompt, _DummyConfigLoader())
    assert transition is not None
    assert transition["name"] == "Cross Dissolve"
    assert transition["duration"] == 0.5


def test_parse_transition_duration_range_ignores_cut_ranges():
    prompt = """
    cut from 3.821s to 38.362s
    cut from 42.383s to 44.294s
    then apply a cross dissolve transition from duration 0.000s to 0.500s
    """
    transition = parse_transition_from_prompt(prompt, _DummyConfigLoader())
    assert transition is not None
    assert transition["duration"] == 0.5


def test_extract_transition_duration_range_phrase():
    prompt = "apply a cross dissolve transition from duration 0.000s to 0.500s"
    assert extract_transition_duration(prompt) == 0.5


def test_parse_transition_beginning_applies_in_only():
    prompt = "Add transition cross dissolve at the beginning of the stock footage."
    transition = parse_transition_from_prompt(prompt, _DummyConfigLoader())
    assert transition is not None
    assert transition["apply_in"] is True
    assert transition["apply_out"] is False


def test_parse_transition_end_applies_out_only():
    prompt = "Add transition cross dissolve at the end of the stock footage."
    transition = parse_transition_from_prompt(prompt, _DummyConfigLoader())
    assert transition is not None
    assert transition["apply_in"] is False
    assert transition["apply_out"] is True
