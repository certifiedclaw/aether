"""v12f: LangChain must not pretend to have researched; live log isolation."""
import integrations.langchain_tools as lt


def test_unavailable_message_tells_model_not_to_claim_research():
    msg = lt._unavailable("research agent", "ddgs")
    assert msg.startswith(lt.UNAVAILABLE_PREFIX)
    assert "Nothing was searched" in msg and "Do NOT say you researched" in msg
    assert "ddgs" in msg


def test_research_agent_import_failure_returns_unavailable(monkeypatch):
    def boom():
        raise ImportError("no langchain")
    monkeypatch.setattr(lt, "_agent_imports", boom)
    out = lt.lc_research_agent("compare python and go")
    assert out.startswith(lt.UNAVAILABLE_PREFIX)
    assert "pip install" not in out.split("Do NOT")[0]  # no bare install hint as the answer


def test_langchain_available_false_when_agent_api_missing(monkeypatch):
    def boom():
        raise ImportError("no langchain")
    monkeypatch.setattr(lt, "_agent_imports", boom)
    assert lt.langchain_available() is False
    assert any("ReAct" in m for m in lt.missing_dependencies())


def test_no_bare_install_strings_left():
    import inspect
    assert '"Install: pip' not in inspect.getsource(lt)


def test_live_report_does_not_touch_real_logs():
    import core.live_report as lr
    from pathlib import Path
    real = Path(lr.__file__).resolve().parent.parent / "logs"
    assert Path(lr._JSONL_PATH).parent != real
    assert Path(lr._REPORT_PATH).parent != real
