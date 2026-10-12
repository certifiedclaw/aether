"""
memory/hybrid_memory.py — Aether Hybrid Memory
ClawMem-style three-stage retrieval: BM25 keyword → dense semantic → cross-encoder rerank.

Why three stages:
  1. BM25       — fast, catches exact names/commands/identifiers
  2. Dense      — semantic similarity for paraphrased queries
  3. Reranker   — cross-encoder re-scores top candidates for final precision

Falls back gracefully when optional packages aren't installed:
  - No sentence-transformers → BM25 only
  - No cross-encoder         → BM25 + dense, no rerank

This replaces the pure-dense approach in memory/semantic_memory.py
for the RAG pipeline. Semantic memory (episodic SQLite store) stays unchanged.
"""
from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

import numpy as np

logger = logging.getLogger(__name__)


# ── BM25 (no dependencies) ────────────────────────────────────────────────

class BM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self._docs: list[str] = []
        self._tf:   list[dict[str, int]] = []
        self._df:   dict[str, int] = defaultdict(int)
        self._avgdl: float = 0.0

    @staticmethod
    def _tok(text: str) -> list[str]:
        return re.findall(r"\b\w+\b", text.lower())

    def build(self, docs: list[str]) -> None:
        self._docs = docs
        self._tf, self._df = [], defaultdict(int)
        total = 0
        for doc in docs:
            toks = self._tok(doc)
            total += len(toks)
            tf: dict[str, int] = defaultdict(int)
            for t in toks:
                tf[t] += 1
            self._tf.append(dict(tf))
            for t in set(toks):
                self._df[t] += 1
        self._avgdl = total / max(len(docs), 1)

    def add_doc(self, doc: str) -> None:
        """Append a single document without re-tokenizing the whole corpus.
        Used on the hot path (every memory store) instead of build(), which
        is O(total corpus size) and gets slower every single message as
        history grows."""
        toks = self._tok(doc)
        tf: dict[str, int] = defaultdict(int)
        for t in toks:
            tf[t] += 1
        self._docs.append(doc)
        self._tf.append(dict(tf))
        for t in set(toks):
            self._df[t] += 1
        n = len(self._docs)
        prev_total = self._avgdl * (n - 1)
        self._avgdl = (prev_total + len(toks)) / n

    def scores(self, query: str) -> list[float]:
        if not self._docs:
            return []
        N = len(self._docs)
        qtoks = self._tok(query)
        out = []
        for i, tf in enumerate(self._tf):
            dl = sum(tf.values())
            s = 0.0
            for t in qtoks:
                if t not in tf:
                    continue
                idf = math.log((N - self._df[t] + 0.5) / (self._df[t] + 0.5) + 1)
                tf_s = tf[t] * (self.k1 + 1) / (
                    tf[t] + self.k1 * (1 - self.b + self.b * dl / self._avgdl))
                s += idf * tf_s
            out.append(s)
        return out


# ── HybridMemory ──────────────────────────────────────────────────────────

class MemoryEntry(NamedTuple):
    id: int
    role: str
    content: str
    timestamp: float
    score: float = 0.0


class HybridMemory:
    """
    Replaces the pure-dense MemoryEngine with a three-stage pipeline.
    API-compatible with MemoryEngine so it can be swapped in as a drop-in.
    """

    def __init__(self, config=None) -> None:
        from memory.config_manager import get_mark_config
        cfg = config or get_mark_config()

        self._db_path = Path(cfg.get("memory.db_path", "./data/memory.db"))
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._threshold     = float(cfg.get("memory.relevance_threshold", 0.3))
        self._history_limit = int(cfg.get("memory.chat_history_limit", 100))
        self._embed_model   = cfg.get("memory.embedding_model", "all-MiniLM-L6-v2")
        self._rerank_model  = cfg.get("memory.rerank_model",
                                       "cross-encoder/ms-marco-MiniLM-L-6-v2")
        self._top_k_fetch   = int(cfg.get("memory.bm25_candidates", 40))
        self._top_k_final   = int(cfg.get("memory.top_k_final", 5))

        self._conn = self._setup_db()
        self._session: list[dict] = []
        self._bm25 = BM25Index()
        self._doc_ids: list[int] = []         # parallel to _bm25 docs
        self._doc_texts: list[str] = []
        self._doc_embeddings: list[np.ndarray | None] = []  # parallel cache, avoids re-encoding

        # Lazy-loaded
        self._encoder = None
        self._reranker = None
        self._enc_tried = self._re_tried = False
        self._enc_lock = threading.Lock()
        self._re_lock  = threading.Lock()

        self._rebuild_bm25()

        # Warm the dense encoder + reranker in the background rather than
        # on the first real retrieval call. Without this, whichever user
        # message triggers the first _retrieve() pays the full multi-second
        # model-load cost inline, stalling that turn. _retrieve() itself
        # uses non-blocking loads (see _load_encoder/_load_reranker below)
        # and just skips the dense/rerank stage for any call that lands
        # before this finishes, so correctness never depends on timing —
        # only how much of the pipeline is available yet.
        threading.Thread(
            target=self._preload_models, daemon=True, name="hybrid-mem-preload"
        ).start()

    # ── DB ────────────────────────────────────────────────────────────────

    def _setup_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("""CREATE TABLE IF NOT EXISTS memories (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            role      TEXT    NOT NULL DEFAULT 'exchange',
            content   TEXT    NOT NULL,
            timestamp REAL    NOT NULL,
            embedding BLOB
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON memories(timestamp DESC)")
        conn.commit()
        return conn

    # ── Lazy loaders ──────────────────────────────────────────────────────
    #
    # `blocking=True` (used by the background preload thread started in
    # __init__) waits for the load to finish. `blocking=False` (used by
    # _retrieve on the hot path) returns immediately if another thread is
    # already loading — a live conversation turn should never stall on a
    # multi-second model load; it just runs BM25-only for that one call and
    # gets the full pipeline on the next one, once loading finishes.

    def _preload_models(self) -> None:
        self._load_encoder(blocking=True)
        self._load_reranker(blocking=True)

    def _load_encoder(self, blocking: bool = True) -> None:
        if self._enc_tried:
            return
        acquired = self._enc_lock.acquire(blocking=blocking)
        if not acquired:
            return  # someone else is already loading it — don't wait
        try:
            if self._enc_tried:  # re-check: may have finished while we waited
                return
            self._enc_tried = True
            try:
                from sentence_transformers import SentenceTransformer
                self._encoder = SentenceTransformer(self._embed_model)
                logger.info("HybridMemory: dense encoder ready (%s)", self._embed_model)
            except ImportError:
                logger.info("HybridMemory: sentence-transformers not installed — BM25 only")
            except Exception as e:
                logger.warning("HybridMemory encoder: %s", e)
        finally:
            self._enc_lock.release()

    def _load_reranker(self, blocking: bool = True) -> None:
        if self._re_tried:
            return
        acquired = self._re_lock.acquire(blocking=blocking)
        if not acquired:
            return
        try:
            if self._re_tried:
                return
            self._re_tried = True
            try:
                from sentence_transformers import CrossEncoder
                self._reranker = CrossEncoder(self._rerank_model)
                logger.info("HybridMemory: cross-encoder reranker ready")
            except ImportError:
                logger.debug("HybridMemory: CrossEncoder not available — skipping rerank")
            except Exception as e:
                logger.debug("HybridMemory reranker: %s", e)
        finally:
            self._re_lock.release()

    # ── Write ─────────────────────────────────────────────────────────────

    def store(self, user_input: str, response: str) -> None:
        ts = time.time()
        combined = f"User: {user_input}\nAssistant: {response}"
        emb = self._encode_one(combined)
        self._conn.execute(
            "INSERT INTO memories(role,content,timestamp,embedding) VALUES(?,?,?,?)",
            ("exchange", combined, ts, emb))
        self._conn.commit()
        # Append to BM25 index incrementally (faster than full rebuild each time)
        row = self._conn.execute(
            "SELECT id FROM memories ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            self._doc_ids.append(row[0])
            self._doc_texts.append(combined)
            self._doc_embeddings.append(
                np.frombuffer(emb, dtype="float32") if emb is not None else None)
            self._bm25.add_doc(combined)  # true incremental update — O(new doc) not O(corpus)
        self._session.append({"role": "user",      "content": user_input})
        self._session.append({"role": "assistant",  "content": response})
        if len(self._session) > self._history_limit:
            self._session = self._session[-self._history_limit:]

    def add_message(self, role: str, content: str) -> None:
        self._session.append({"role": role, "content": content})
        if len(self._session) > self._history_limit:
            self._session = self._session[-self._history_limit:]

    # ── Read ──────────────────────────────────────────────────────────────

    def get_session(self) -> list[dict]:
        return list(self._session)

    def clear_session(self) -> None:
        self._session.clear()

    def get_recent(self, n: int = 10) -> list[MemoryEntry]:
        rows = self._conn.execute(
            "SELECT id,role,content,timestamp FROM memories "
            "ORDER BY timestamp DESC LIMIT ?", (n,)).fetchall()
        return [MemoryEntry(r[0], r[1], r[2], r[3]) for r in rows]

    def inject_context(self, user_input: str, system_prompt: str) -> str:
        memories = self._retrieve(user_input, top_k=4)
        if not memories:
            return system_prompt
        snippets = "\n".join(f"- {m.content[:300]}" for m in memories)
        return system_prompt + f"\n\n[Relevant memory]\n{snippets}\n[/memory]"

    def stats(self) -> dict:
        total = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        return {
            "total_memories":   total,
            "session_messages": len(self._session),
            "semantic":         self._encoder is not None,
            "reranker":         self._reranker is not None,
            "bm25_docs":        len(self._doc_texts),
        }

    # ── Three-stage retrieval ─────────────────────────────────────────────

    def _retrieve(self, query: str, top_k: int = 5) -> list[MemoryEntry]:
        # NOTE: these early-outs return [] rather than get_recent(top_k).
        # get_recent() is a legitimate direct API (used by callers who want
        # "the last N exchanges" on purpose) but using it as a silent
        # substitute here — whenever no doc index exists yet, BM25 finds
        # nothing, or nothing clears the relevance threshold — meant every
        # "no relevant memory" case quietly injected unrelated recent
        # conversation into the prompt as if it *were* relevant. A greeting
        # or an unrelated follow-up would get whatever was chatted about
        # last, framed as "[Relevant memory]". No match should mean no
        # injected context, not a random substitute.
        if not self._doc_texts:
            return []

        # Stage 1 — BM25 candidate selection
        bm25_raw = self._bm25.scores(query)
        if not bm25_raw:
            return []

        max_bm25 = max(bm25_raw) or 1.0
        ranked = sorted(
            enumerate(bm25_raw), key=lambda x: x[1], reverse=True
        )[:self._top_k_fetch]
        candidates = [(i, self._doc_ids[i], self._doc_texts[i], s / max_bm25)
                      for i, s in ranked if s > 0]
        if not candidates:
            return []

        # Stage 2 — Dense re-scoring (if encoder available). Non-blocking:
        # a turn that lands before the background preload finishes just
        # runs BM25-only rather than stalling on model load.
        self._load_encoder(blocking=False)
        if self._encoder:
            try:
                # Reuse cached embeddings computed at write-time; only encode
                # candidates that don't have one yet (e.g. encoder wasn't
                # available when they were stored), then backfill the cache.
                missing = [idx for idx, _, _, _ in candidates
                           if self._doc_embeddings[idx] is None]
                if missing:
                    new_vecs = self._encoder.encode(
                        [self._doc_texts[idx] for idx in missing]).astype("float32")
                    for idx, vec in zip(missing, new_vecs):
                        self._doc_embeddings[idx] = vec

                q_vec  = self._encoder.encode(query)
                c_vecs = np.stack([self._doc_embeddings[idx] for idx, _, _, _ in candidates])
                sims   = (c_vecs @ q_vec) / (
                    np.linalg.norm(c_vecs, axis=1) * np.linalg.norm(q_vec) + 1e-9)
                max_s  = float(sims.max()) or 1.0
                # Combine BM25 (40%) + dense (60%)
                candidates = [
                    (cid, txt, 0.4 * bm25_s + 0.6 * float(sims[i]) / max_s)
                    for i, (_idx, cid, txt, bm25_s) in enumerate(candidates)
                ]
                candidates.sort(key=lambda x: x[2], reverse=True)
            except Exception as e:
                logger.debug("Dense scoring error: %s", e)
                candidates = [(cid, txt, bm25_s) for _idx, cid, txt, bm25_s in candidates]
        else:
            candidates = [(cid, txt, bm25_s) for _idx, cid, txt, bm25_s in candidates]

        # Stage 3 — Cross-encoder reranking (if available)
        top_candidates = candidates[:top_k * 2]
        # Non-blocking for the same reason as the encoder above.
        self._load_reranker(blocking=False)
        if self._reranker and len(top_candidates) > 1:
            try:
                pairs  = [(query, c[1]) for c in top_candidates]
                scores = self._reranker.predict(pairs)
                top_candidates = [
                    (c[0], c[1], float(s))
                    for c, s in sorted(
                        zip(top_candidates, scores),
                        key=lambda x: x[1], reverse=True
                    )
                ]
            except Exception as e:
                logger.debug("Reranker error: %s", e)

        # Return top-k as MemoryEntry
        result = []
        for doc_id, text, score in top_candidates[:top_k]:
            if score >= self._threshold:
                result.append(MemoryEntry(doc_id, "exchange", text, 0.0, score))
        return result

    # ── Helpers ───────────────────────────────────────────────────────────

    def _encode_one(self, text: str) -> bytes | None:
        # Non-blocking — a memory write shouldn't stall the turn either;
        # it just skips the embedding (stored with embedding=NULL) if the
        # encoder isn't ready yet, same as a cache miss.
        self._load_encoder(blocking=False)
        if self._encoder is None:
            return None
        try:
            return self._encoder.encode(text).astype("float32").tobytes()
        except Exception:
            return None

    def _rebuild_bm25(self) -> None:
        rows = self._conn.execute(
            "SELECT id, content, embedding FROM memories ORDER BY id ASC").fetchall()
        self._doc_ids   = [r[0] for r in rows]
        self._doc_texts = [r[1] for r in rows]
        self._doc_embeddings = [
            np.frombuffer(r[2], dtype="float32") if r[2] is not None else None
            for r in rows
        ]
        if self._doc_texts:
            self._bm25.build(self._doc_texts)
