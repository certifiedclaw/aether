"""
memory_engine.py — Aether v3 Memory Engine
SQLite-backed episodic memory + in-session working memory.
Encoder is LAZY — loads on first query, never blocks startup.
"""
from __future__ import annotations
import logging, sqlite3, time
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)


class MemoryEntry(NamedTuple):
    id: int
    role: str
    content: str
    timestamp: float


class MemoryEngine:
    def __init__(self, config=None) -> None:
        from memory.config_manager import get_mark_config as get_config
        cfg = config or get_config()
        self._db_path = Path(cfg.get("memory.db_path", "./data/memory.db"))
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._threshold     = float(cfg.get("memory.relevance_threshold", 0.35))
        self._history_limit = int(cfg.get("memory.chat_history_limit", 100))
        self._embedding_model = cfg.get("memory.embedding_model", "all-MiniLM-L6-v2")

        self._conn = self._connect()
        self._session: list[dict] = []

        # Encoder is intentionally NOT loaded here — loading SentenceTransformer
        # can take 5-30 seconds on first run and blocks the loading screen.
        # It is loaded lazily on first memory query instead.
        self._encoder = None
        self._encoder_tried = False

        # In-memory cache of the most recent embeddings, mirroring
        # hybrid_memory.py's approach: loaded once (in _load_embedding_cache,
        # triggered when the encoder first becomes available) instead of
        # re-querying + re-decoding BLOBs from SQLite on every single search.
        # Kept in sync incrementally by store() afterwards. Newest-first,
        # same ordering/window as the old "ORDER BY timestamp DESC LIMIT 200".
        self._cache_max: int = 200
        self._cache_ids:   list[int]   = []
        self._cache_texts: list[str]   = []
        self._cache_ts:    list[float] = []
        self._cache_vecs:  list        = []  # np.ndarray, parallel to the above

    # ── DB setup ──────────────────────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
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

    # ── Lazy encoder ──────────────────────────────────────────────────────────
    def _ensure_encoder(self) -> None:
        """Load sentence-transformers only on first use, not at startup."""
        if self._encoder_tried:
            return
        self._encoder_tried = True
        try:
            from sentence_transformers import SentenceTransformer
            self._encoder = SentenceTransformer(self._embedding_model)
            logger.info("Semantic memory enabled (%s)", self._embedding_model)
            self._load_embedding_cache()
        except ImportError:
            logger.info("sentence-transformers not installed — recency-only memory")
        except Exception as e:
            logger.warning("Encoder load failed: %s", e)

    def _load_embedding_cache(self) -> None:
        """One-time load of the most recent embedded rows into memory.

        Runs once, right after the encoder becomes available (rows stored
        before that point have no embedding to load — same as before, they
        just never take part in semantic search). After this, store()
        appends to the cache directly so _semantic_search never has to hit
        SQLite again.
        """
        try:
            import numpy as np
            rows = self._conn.execute(
                "SELECT id,content,timestamp,embedding FROM memories "
                "WHERE embedding IS NOT NULL "
                "ORDER BY timestamp DESC LIMIT ?", (self._cache_max,)).fetchall()
        except Exception:
            return
        self._cache_ids   = [r[0] for r in rows]
        self._cache_texts = [r[1] for r in rows]
        self._cache_ts    = [r[2] for r in rows]
        self._cache_vecs  = [np.frombuffer(r[3], dtype=np.float32) for r in rows]

    # ── Write ──────────────────────────────────────────────────────────────────
    def store(self, user_input: str, response: str) -> None:
        ts  = time.time()
        combined = f"User: {user_input}\nAssistant: {response}"
        emb = self._encode(combined)
        cur = self._conn.execute(
            "INSERT INTO memories(role,content,timestamp,embedding) VALUES(?,?,?,?)",
            ("exchange", combined, ts, emb))
        self._conn.commit()
        if emb is not None:
            # Prepend (cache is newest-first) and append incrementally —
            # no re-read from SQLite needed, mirrors hybrid_memory.py.
            import numpy as np
            self._cache_ids.insert(0, cur.lastrowid)
            self._cache_texts.insert(0, combined)
            self._cache_ts.insert(0, ts)
            self._cache_vecs.insert(0, np.frombuffer(emb, dtype=np.float32))
            if len(self._cache_ids) > self._cache_max:
                self._cache_ids.pop()
                self._cache_texts.pop()
                self._cache_ts.pop()
                self._cache_vecs.pop()
        self._session.append({"role": "user",      "content": user_input})
        self._session.append({"role": "assistant",  "content": response})
        if len(self._session) > self._history_limit:
            self._session = self._session[-self._history_limit:]

    def add_message(self, role: str, content: str) -> None:
        self._session.append({"role": role, "content": content})
        if len(self._session) > self._history_limit:
            self._session = self._session[-self._history_limit:]

    # ── Read ───────────────────────────────────────────────────────────────────
    def get_session(self) -> list[dict]:
        return list(self._session)

    def clear_session(self) -> None:
        self._session.clear()

    def get_recent(self, n: int = 10) -> list[MemoryEntry]:
        cur = self._conn.execute(
            "SELECT id,role,content,timestamp FROM memories "
            "ORDER BY timestamp DESC LIMIT ?", (n,))
        return [MemoryEntry(*row) for row in cur.fetchall()]

    def inject_context(self, user_input: str, system_prompt: str) -> str:
        """Add relevant past memories to the system prompt."""
        memories = self._retrieve(user_input, top_k=4)
        if not memories:
            return system_prompt
        snippets = "\n".join(f"- {m.content[:300]}" for m in memories)
        return system_prompt + f"\n\n[Relevant memory]\n{snippets}\n[/memory]"

    def _retrieve(self, query: str, top_k: int = 4) -> list[MemoryEntry]:
        self._ensure_encoder()
        if self._encoder:
            return self._semantic_search(query, top_k)
        # No encoder available at all (capability absent, e.g. optional
        # dependency not installed) — different situation from "searched
        # and found no relevant match" below, so falling back to recent
        # exchanges here is a deliberate degraded-mode choice, not the
        # same relevance-fallback bug.
        return self.get_recent(top_k)

    def _semantic_search(self, query: str, top_k: int) -> list[MemoryEntry]:
        try:
            import numpy as np
            q_vec = self._encoder.encode(query)
            scored = []
            for i, vec in enumerate(self._cache_vecs):
                score = float(np.dot(q_vec, vec) /
                              (np.linalg.norm(q_vec) * np.linalg.norm(vec) + 1e-9))
                if score >= self._threshold:
                    entry = MemoryEntry(self._cache_ids[i], "exchange",
                                         self._cache_texts[i], self._cache_ts[i])
                    scored.append((score, entry))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [e for _, e in scored[:top_k]]
        except Exception:
            # A search that errors out found nothing relevant, same as one
            # that legitimately scored below threshold — return no context
            # rather than silently substituting unrelated recent exchanges.
            return []

    def _encode(self, text: str) -> bytes | None:
        """Encode only if encoder is already loaded — never trigger a load during store()."""
        if not self._encoder:
            return None
        try:
            import numpy as np
            return self._encoder.encode(text).astype(np.float32).tobytes()
        except Exception:
            return None

    # ── Stats ──────────────────────────────────────────────────────────────────
    def stats(self) -> dict:
        total = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        return {
            "total_memories": total,
            "session_messages": len(self._session),
            "semantic": self._encoder is not None,
        }

    def close(self) -> None:
        self._conn.close()
