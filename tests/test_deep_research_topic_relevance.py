from actions import deep_research as research
from actions import web_search


def test_planner_falls_back_when_queries_lose_the_subject(monkeypatch):
    from core import llm_client

    monkeypatch.setattr(
        llm_client,
        "call_llm_text",
        lambda *args, **kwargs: (
            '{"plan":["Assess conservation status"],'
            '"queries":["current", "major", "global"]}'
        ),
    )

    plan, queries = research._plan(
        "Do deep research on sea turtles. Cover conservation status, threats, and recovery efforts."
    )

    assert plan
    assert len(queries) >= 3
    assert all("turtle" in query.casefold() for query in queries)
    assert any("threat" in query.casefold() for query in queries)
    assert any("recovery" in query.casefold() for query in queries)


def test_topic_filter_rejects_unrelated_search_results():
    topic = "Do deep research on sea turtles. Cover conservation status and threats."

    assert not research._topic_relevant(
        {"title": "Current — Future of Banking", "snippet": "Major global finance news."},
        topic,
    )
    assert research._topic_relevant(
        {"title": "Sea turtle conservation", "snippet": "Green turtles face threats."},
        topic,
    )


def test_deep_research_stops_instead_of_synthesizing_unrelated_results(monkeypatch):
    monkeypatch.setattr(research, "_plan", lambda topic: ([], ["sea turtle overview"]))
    monkeypatch.setattr(
        web_search,
        "_ddg_search",
        lambda query, max_results: [{
            "title": "Current support center",
            "snippet": "Major global banking services.",
            "url": "https://example.org/current",
        }],
    )

    result = research.deep_research("sea turtles conservation status")

    assert result.startswith("Deep research could not find relevant web sources")
