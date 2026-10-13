import threading

from core import llm_client


def test_openai_stream_stops_at_next_chunk(monkeypatch):
    cancel = threading.Event()

    class Response:
        status_code = 200
        text = ""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_lines(self):
            yield b'data: {"choices":[{"delta":{"content":"Partial answer."},"finish_reason":null}]}'
            cancel.set()
            yield b'data: {"choices":[{"delta":{"content":"should not be read"},"finish_reason":null}]}'

    monkeypatch.setattr(llm_client, "_load_config", lambda: {
        "llm_provider": "openai_api", "llm_url": "https://api.openai.com",
        "llm_model": "test", "openai_api_key": "test",
    })
    monkeypatch.setattr(llm_client.requests, "post", lambda *args, **kwargs: Response())

    events = list(llm_client.call_llm_stream(
        [{"role": "user", "content": "hello"}], cancel_event=cancel,
    ))

    assert events[-1] == {"type": "cancelled", "content": "Partial answer."}
    assert all(event.get("type") != "done" for event in events)


def test_ollama_stream_stops_before_next_chunk(monkeypatch):
    cancel = threading.Event()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            pass

        def iter_lines(self):
            yield b'{"message":{"content":"Partial answer."},"done":false}'
            cancel.set()
            yield b'{"message":{"content":"should not be read"},"done":true}'

    monkeypatch.setattr(llm_client, "get_llm_provider", lambda: "ollama")
    monkeypatch.setattr(llm_client, "get_llm_settings", lambda: ("http://localhost:11434", "test"))
    monkeypatch.setattr(llm_client.requests, "post", lambda *args, **kwargs: Response())

    events = list(llm_client.call_llm_stream(
        [{"role": "user", "content": "hello"}], cancel_event=cancel,
    ))

    assert events[-1] == {"type": "cancelled", "content": "Partial answer."}
    assert all(event.get("type") != "done" for event in events)


def test_openai_cancel_closes_response_during_blocked_read(monkeypatch):
    cancel = threading.Event()
    started = threading.Event()
    released = threading.Event()

    class Response:
        status_code = 200
        text = ""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_lines(self):
            yield b'data: {"choices":[{"delta":{"content":"Partial answer."},"finish_reason":null}]}'
            started.set()
            released.wait(3)
            if False:
                yield b""

        def close(self):
            released.set()

    monkeypatch.setattr(llm_client, "_load_config", lambda: {
        "llm_provider": "openai_api", "llm_url": "https://api.openai.com",
        "llm_model": "test", "openai_api_key": "test",
    })
    monkeypatch.setattr(llm_client.requests, "post", lambda *args, **kwargs: Response())
    events = []
    worker = threading.Thread(target=lambda: events.extend(llm_client.call_llm_stream(
        [{"role": "user", "content": "hello"}], cancel_event=cancel,
    )), daemon=True)
    worker.start()

    assert started.wait(2)
    cancel.set()
    worker.join(2)

    assert not worker.is_alive()
    assert events[-1] == {"type": "cancelled", "content": "Partial answer."}
    assert all(event.get("type") != "done" for event in events)


def test_ollama_cancel_closes_response_during_blocked_read(monkeypatch):
    cancel = threading.Event()
    started = threading.Event()
    released = threading.Event()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            pass

        def iter_lines(self):
            yield b'{"message":{"content":"Partial answer."},"done":false}'
            started.set()
            released.wait(3)
            if False:
                yield b""

        def close(self):
            released.set()

    monkeypatch.setattr(llm_client, "get_llm_provider", lambda: "ollama")
    monkeypatch.setattr(llm_client, "get_llm_settings", lambda: ("http://localhost:11434", "test"))
    monkeypatch.setattr(llm_client.requests, "post", lambda *args, **kwargs: Response())
    events = []
    worker = threading.Thread(target=lambda: events.extend(llm_client.call_llm_stream(
        [{"role": "user", "content": "hello"}], cancel_event=cancel,
    )), daemon=True)
    worker.start()

    assert started.wait(2)
    cancel.set()
    worker.join(2)

    assert not worker.is_alive()
    assert events[-1] == {"type": "cancelled", "content": "Partial answer."}
    assert all(event.get("type") != "done" for event in events)
