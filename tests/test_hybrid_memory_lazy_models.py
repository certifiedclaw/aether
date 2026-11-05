import threading

from memory.hybrid_memory import HybridMemory


def test_hybrid_models_do_not_preload_by_default(tmp_path):
    memory = HybridMemory(config={"memory.db_path": str(tmp_path / "memory.db")})

    assert memory._encoder is None
    assert memory._reranker is None
    assert not memory._enc_tried
    assert not memory._re_tried


def test_hybrid_encoder_load_is_started_in_background_on_demand(tmp_path, monkeypatch):
    memory = HybridMemory(config={"memory.db_path": str(tmp_path / "memory.db")})
    load_started = threading.Event()
    finish_load = threading.Event()
    call_returned = threading.Event()

    def blocked_load():
        load_started.set()
        finish_load.wait(2)

    monkeypatch.setattr(memory, "_load_encoder_model", blocked_load)

    caller = threading.Thread(
        target=lambda: (memory._load_encoder(blocking=False), call_returned.set())
    )
    caller.start()
    try:
        assert load_started.wait(1)
        assert call_returned.wait(1)
    finally:
        finish_load.set()
        caller.join(1)

    assert not caller.is_alive()
    assert memory._enc_tried


def test_single_candidate_does_not_load_reranker(tmp_path, monkeypatch):
    memory = HybridMemory(config={"memory.db_path": str(tmp_path / "memory.db")})
    memory._doc_texts = ["User: project deadline\nAssistant: Friday"]
    memory._doc_ids = [1]
    memory._doc_embeddings = [None]
    memory._bm25.scores = lambda _query: [1.0]
    monkeypatch.setattr(memory, "_load_encoder", lambda **_kwargs: None)
    reranker_loads = []
    monkeypatch.setattr(
        memory, "_load_reranker",
        lambda **_kwargs: reranker_loads.append(True),
    )

    memory._retrieve("project deadline", top_k=1)

    assert reranker_loads == []


def test_hybrid_model_preload_can_be_enabled(tmp_path, monkeypatch):
    preload_started = threading.Event()
    monkeypatch.setattr(
        HybridMemory, "_preload_models", lambda _self: preload_started.set()
    )

    HybridMemory(config={
        "memory.db_path": str(tmp_path / "memory.db"),
        "memory.preload_models": True,
    })

    assert preload_started.wait(1)
