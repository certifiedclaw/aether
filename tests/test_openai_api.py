from core import llm_client


def test_openai_api_stream_uses_authenticated_chat_completions(monkeypatch):
    config = {
        "llm_provider": "openai_api",
        "llm_url": "https://api.openai.com",
        "llm_model": "test-model",
        "openai_api_key": "test-key",
        "openai_max_tokens": 900,
    }
    monkeypatch.setattr(llm_client, "_load_config", lambda: config)
    monkeypatch.delenv("AETHER_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    request = {}

    class Response:
        status_code = 200
        text = ""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_lines(self):
            return iter([
                b'data: {"choices":[{"delta":{"content":"Hello."},"finish_reason":null}]}',
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            ])

    def post(url, **kwargs):
        request["url"] = url
        request.update(kwargs)
        return Response()

    monkeypatch.setattr(llm_client.requests, "post", post)
    events = list(llm_client.call_llm_stream(
        [{"role": "user", "content": "Hi"}],
        [{"type": "function", "function": {"name": "test", "parameters": {}}}],
    ))

    assert request["url"] == "https://api.openai.com/v1/chat/completions"
    assert request["headers"] == {"Authorization": "Bearer test-key"}
    assert request["json"]["model"] == "test-model"
    assert request["json"]["max_completion_tokens"] == 900
    assert request["json"]["tools"][0]["function"]["name"] == "test"
    assert any(event.get("text") == "Hello." for event in events)
    assert events[-1]["type"] == "done"


def test_openai_api_provider_and_token_limit(monkeypatch):
    monkeypatch.setattr(llm_client, "_load_config", lambda: {
        "llm_provider": "openai_api", "openai_max_tokens": 99999,
    })
    assert llm_client.get_llm_provider() == "openai_api"
    assert llm_client._chat_completions_endpoint("https://api.openai.com", "openai_api") == (
        "https://api.openai.com/v1/chat/completions"
    )
    assert llm_client._cloud_max_tokens("openai_api") == 8192


def test_openai_api_nonstream_preserves_tool_calls(monkeypatch):
    config = {
        "llm_provider": "openai_api",
        "llm_url": "https://api.openai.com",
        "llm_model": "test-model",
        "openai_api_key": "test-key",
    }
    monkeypatch.setattr(llm_client, "_load_config", lambda: config)
    seen = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {
                "content": "", "tool_calls": [{"id": "call-1", "function": {
                    "name": "test_tool", "arguments": "{}",
                }}],
            }}]}

    def post(url, **kwargs):
        seen["url"], seen["kwargs"] = url, kwargs
        return Response()

    monkeypatch.setattr(llm_client.requests, "post", post)
    result = llm_client.call_llm(
        [{"role": "user", "content": "do it"}],
        [{"type": "function", "function": {"name": "test_tool", "parameters": {}}}],
    )

    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert seen["kwargs"]["headers"] == {"Authorization": "Bearer test-key"}
    assert seen["kwargs"]["json"]["tool_choice"] == "auto"
    assert result["tool_calls"][0]["function"]["name"] == "test_tool"
