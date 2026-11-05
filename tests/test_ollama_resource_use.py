import json

from core import llm_client


def test_model_startup_warmup_is_opt_in():
    assert llm_client.should_warmup_on_start({}) is False
    assert llm_client.should_warmup_on_start({"llm_warmup_on_start": True}) is True
    assert llm_client.should_warmup_on_start({"llm_warmup_on_start": "yes"}) is True
    assert llm_client.should_warmup_on_start({"llm_warmup_on_start": "false"}) is False


def test_ollama_keep_alive_defaults_to_five_minutes(monkeypatch):
    monkeypatch.setattr(llm_client, "_load_config", lambda: {})
    assert llm_client._ollama_keep_alive() == "5m"


def test_ollama_requests_use_configured_keep_alive(monkeypatch):
    payloads = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "Hello"}}

        def iter_lines(self):
            yield json.dumps({"message": {"content": "Hello"}, "done": True}).encode()

    def post(_url, *, json, **_kwargs):
        payloads.append(json)
        return Response()

    monkeypatch.setattr(llm_client, "_load_config", lambda: {"llm_keep_alive": "45s"})
    monkeypatch.setattr(
        llm_client, "get_llm_settings",
        lambda: ("http://localhost:11434", "test-model"),
    )
    monkeypatch.setattr(llm_client, "get_llm_provider", lambda: "ollama")
    monkeypatch.setattr(llm_client.requests, "post", post)

    llm_client.warmup_model()
    llm_client._call_llm_impl([{"role": "user", "content": "hello"}])
    llm_client._call_llm_text_impl("hello")
    list(llm_client._call_llm_stream_impl([{"role": "user", "content": "hello"}]))

    assert len(payloads) == 4
    assert [payload["keep_alive"] for payload in payloads] == ["45s"] * 4
