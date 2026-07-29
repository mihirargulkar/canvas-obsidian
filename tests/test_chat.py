from canvas_vault.chat import route

def test_route_deadline_vs_semantic():
    assert route("what's due this week") == "deadline"
    assert route("when is homework due") == "deadline"
    assert route("explain gradient descent") == "semantic"
    assert route("what is L2 regularization") == "semantic"
