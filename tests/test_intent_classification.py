from core.assistant import classify_intent


def test_edit_prompt_with_transition_duration_routes_edit_request():
    message = """
    Cut from 3.821s to 38.362s
    then apply a cross dissolve transition at 0.000s with a duration of 0.5s
    then add captions with font size 24 and yellow color
    """
    result = classify_intent(message, [])
    assert result["intent"] == "edit_request"
    assert result["route"] == "implementation"


def test_duration_question_routes_video_info():
    message = "What is the video duration and fps?"
    result = classify_intent(message, [])
    assert result["intent"] == "video_info"
    assert result["route"] == "implementation"


def test_edit_question_with_duration_still_routes_edit_request():
    message = "Can you cut from 10s to 20s and add a 0.5s transition?"
    result = classify_intent(message, [])
    assert result["intent"] == "edit_request"
    assert result["route"] == "implementation"
