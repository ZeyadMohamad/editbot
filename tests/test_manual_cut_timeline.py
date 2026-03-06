from tools.silence_cutter_tool import (
    _build_keep_segments,
    _merge_segments,
    parse_manual_cut_segments_from_prompt,
)


def test_manual_cut_parser_extracts_multiple_ranges():
    prompt = """
    cut from 3.821s to 38.362s
    cut from 42.383s to 44.294s
    cut from 46.3s to 49.537s
    """
    segments = parse_manual_cut_segments_from_prompt(prompt)
    assert len(segments) == 3
    assert segments[0] == {"start": 3.821, "end": 38.362}
    assert segments[1] == {"start": 42.383, "end": 44.294}
    assert segments[2] == {"start": 46.3, "end": 49.537}


def test_keep_segments_are_computed_from_original_timeline():
    duration = 20.0
    manual_cuts = [
        {"start": 8.0, "end": 10.0, "type": "manual"},
        {"start": 2.0, "end": 4.0, "type": "manual"},
        {"start": 12.0, "end": 14.0, "type": "manual"},
    ]
    cut_segments = _merge_segments(manual_cuts)
    keep_segments = _build_keep_segments(duration, cut_segments)

    assert keep_segments == [
        {"start": 0.0, "end": 2.0, "duration": 2.0},
        {"start": 4.0, "end": 8.0, "duration": 4.0},
        {"start": 10.0, "end": 12.0, "duration": 2.0},
        {"start": 14.0, "end": 20.0, "duration": 6.0},
    ]
