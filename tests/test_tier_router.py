from core.tier_router import Tier, classify, route_and_generate


def test_route_and_generate_passes_followup_context():
    response, decision = route_and_generate(
        "yes",
        has_active_task=False,
        follows_turn=True,
        call_ollama=lambda model, message: "continued",
    )

    assert response == "continued"
    assert decision.tier == Tier.EVERYDAY
    assert classify("yes", follows_turn=True).tier == decision.tier


def test_hard_validation_failure_does_not_repeat_gemini():
    calls = []

    response, decision = route_and_generate(
        "Analyze this architecture",
        has_active_task=False,
        call_gemini=lambda message: calls.append(message) or "invalid",
        validate=lambda result: False,
    )

    assert calls == ["Analyze this architecture"]
    assert response.startswith("Error: response validation failed")
    assert decision.tier == Tier.HARD


def test_escalation_validation_failure_returns_explicit_error():
    calls = []

    response, decision = route_and_generate(
        "Tell me a short fact",
        has_active_task=False,
        call_ollama=lambda model, message: calls.append(("local", model)) or "invalid",
        call_gemini=lambda message: calls.append(("gemini", message)) or "still invalid",
        validate=lambda result: False,
    )

    assert len(calls) == 2
    assert calls[0][0] == "local"
    assert calls[1][0] == "gemini"
    assert response.startswith("Error: response validation failed")
    assert decision.tier == Tier.HARD
