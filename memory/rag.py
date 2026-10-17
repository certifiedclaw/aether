"""
memory/rag.py — Aether Hybrid RAG Engine
BM25 + dense vector retrieval (upgraded from pure-dense).
"""
from __future__ import annotations
import json, logging, math, os, re
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)
_EXTS = {".pdf", ".md", ".txt", ".rst", ".csv", ".docx"}


class _BM25:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self._docs: list[str] = []; self._tf: list[dict] = []
        self._df: dict = defaultdict(int); self._avgdl: float = 0.0

    @staticmethod
    def _tok(t: str) -> list[str]: return re.findall(r"\b\w+\b", t.lower())

    def build(self, docs: list[str]) -> None:
        self._docs = docs; self._tf, self._df = [], defaultdict(int); total = 0
        for doc in docs:
            toks = self._tok(doc); total += len(toks)
            tf: dict = defaultdict(int)
            for t in toks: tf[t] += 1
            self._tf.append(dict(tf))
            for t in set(toks): self._df[t] += 1
        self._avgdl = total / max(len(docs), 1)

    def query(self, q: str, top_k: int = 10) -> list[tuple[float, int]]:
        if not self._docs: return []
        N = len(self._docs); toks = self._tok(q); scores = []
        for i, tf in enumerate(self._tf):
            dl = sum(tf.values()); s = 0.0
            for t in toks:
                if t not in tf: continue
                idf = math.log((N - self._df[t] + 0.5) / (self._df[t] + 0.5) + 1)
                tf_s = tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * dl / self._avgdl))
                s += idf * tf_s
            scores.append((s, i))
        scores.sort(reverse=True); return scores[:top_k]


class RAGEngine:
    def __init__(self, config=None) -> None:
        from memory.config_manager import get_mark_config
        cfg = config or get_mark_config()
        self._index_path = Path(cfg.get("rag.index_path", "./data/rag_index"))
        self._chunk_size = int(cfg.get("rag.chunk_size", 500))
        self._overlap    = int(cfg.get("rag.chunk_overlap", 50))
        self._top_k      = int(cfg.get("rag.top_k", 5))
        self._index_path.mkdir(parents=True, exist_ok=True)
        self._chunks: list[dict] = []; self._embeddings = None
        self._encoder = None; self._enc_tried = False; self._bm25 = _BM25()
        self._load_index()

    def _try_encoder(self) -> None:
        if self._enc_tried: return
        self._enc_tried = True
        try:
            from sentence_transformers import SentenceTransformer
            self._encoder = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("RAG: dense encoder loaded")
        except ImportError: logger.info("RAG: BM25 only (install sentence-transformers for dense search)")
        except Exception as e: logger.warning("RAG encoder: %s", e)

    def index_file(self, path: str, _defer: bool = False) -> str:
        p = Path(os.path.expanduser(path))
        if not p.exists(): return f"Not found: {path}"
        if p.suffix.lower() not in _EXTS: return f"Unsupported: {p.suffix}"
        text = self._read(p)
        if not text: return f"Could not read: {p.name}"
        self._chunks = [c for c in self._chunks if c["source"] != str(p)]
        nc = self._chunk(text, p); self._chunks.extend(nc)
        if not _defer:
            self._rebuild(); self._save()
        return f"Indexed {p.name} ({len(nc)} chunks, {len(self._chunks)} total)"

    def index_directory(self, dir_path: str) -> str:
        d = Path(os.path.expanduser(dir_path))
        if not d.exists(): return f"Not found: {dir_path}"
        n = 0
        # Chunk every file first, then rebuild the BM25 index and re-encode
        # embeddings ONCE at the end. Previously each index_file() call
        # triggered a full rebuild+re-embed of every chunk seen so far, so
        # indexing a directory of N files did the embedding work ~N times
        # over (quadratic in the number of files).
        for root, _, files in os.walk(d):
            for fname in files:
                if Path(fname).suffix.lower() in _EXTS:
                    self.index_file(str(Path(root) / fname), _defer=True); n += 1
        if n:
            self._rebuild(); self._save()
        return f"Indexed {n} file(s) from {dir_path}"

    def _read(self, path: Path) -> str:
        try:
            if path.suffix.lower() == ".pdf":
                try:
                    import fitz
                    doc = fitz.open(str(path))
                    try:
                        return "\n\n".join(
                            f"[Page {i + 1}]\n{page.get_text()}"
                            for i, page in enumerate(doc)
                        )
                    finally:
                        doc.close()
                except ImportError: return ""
            if path.suffix.lower() == ".docx":
                try:
                    from docx import Document
                    return "\n".join(p.text for p in Document(str(path)).paragraphs)
                except ImportError: return ""
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception: return ""

    def _chunk(self, text: str, source: Path) -> list[dict]:
        step = max(1, self._chunk_size - self._overlap)
        markers = list(re.finditer(r"\[Page\s+(\d+)\]\s*", text))
        segments = []
        if markers:
            for index, marker in enumerate(markers):
                end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
                segments.append((text[marker.end():end], marker.end(), int(marker.group(1))))
        else:
            segments.append((text, 0, None))

        chunks = []
        for segment, segment_offset, page in segments:
            for i in range(0, len(segment), step):
                chunk_text = segment[i:i + self._chunk_size].strip()
                if len(chunk_text) < 40:
                    continue
                chunks.append({
                    "text": chunk_text,
                    "source": str(source),
                    "offset": segment_offset + i,
                    "page": page,
                })
        return chunks

    def query(self, question: str, router=None) -> str:
        if not self._chunks: return "No documents indexed. Use index_file() or index_directory()."
        relevant = self._hybrid_retrieve(question)
        if not relevant: return "No relevant content found."
        entries = []
        sources = []
        for i, chunk in enumerate(relevant, 1):
            filename = Path(chunk["source"]).name
            page = chunk.get("page")
            chunk_number = chunk.get("offset", 0) // max(1, self._chunk_size - self._overlap) + 1
            location = f"{filename}, page {page}" if page else f"{filename}, chunk {chunk_number}"
            sources.append(f"[{i}] {location}")
            entries.append(f"[{i}] {location}\n{chunk['text']}")
        context = "\n\n---\n\n".join(entries)
        if not router:
            answer = f"Relevant context:\n\n{context[:2000]}"
        else:
            prompt = (f"Answer using ONLY the context below. Cite supported facts with the matching "
                      f"source marker such as [1]. Say 'not found' if the answer isn't there.\n\n"
                  f"Context:\n{context}\n\nQuestion: {question}")
            try:
                from core.llm_client import call_llm_text
                answer = call_llm_text(prompt)
            except Exception as e:
                return f"RAG query error: {e}"
        return f"{answer}\n\nSources:\n" + "\n".join(sources)

    def _hybrid_retrieve(self, query: str) -> list[dict]:
        n = len(self._chunks)
        if n == 0: return []
        bm25_raw = self._bm25.query(query, top_k=n)
        bm25_map: dict[int, float] = {}
        if bm25_raw:
            max_s = max(s for s, _ in bm25_raw) or 1.0
            for s, i in bm25_raw: bm25_map[i] = s / max_s
        dense_map: dict[int, float] = {}
        self._try_encoder()
        if self._encoder is not None and self._embeddings is not None:
            try:
                import numpy as np
                q_vec = self._encoder.encode(query)
                sims  = (self._embeddings @ q_vec) / (np.linalg.norm(self._embeddings, axis=1) * np.linalg.norm(q_vec) + 1e-9)
                max_s = float(sims.max()) or 1.0
                for i, s in enumerate(sims): dense_map[i] = float(s) / max_s
            except Exception: pass
        combined = sorted(range(n), key=lambda i: 0.4 * bm25_map.get(i, 0) + 0.6 * dense_map.get(i, 0), reverse=True)
        return [self._chunks[i] for i in combined[:self._top_k] if bm25_map.get(i, 0) > 0.05 or dense_map.get(i, 0) > 0.1]

    def _rebuild(self) -> None:
        texts = [c["text"] for c in self._chunks]; self._bm25.build(texts)
        self._try_encoder()
        if self._encoder and texts:
            try: self._embeddings = self._encoder.encode(texts, show_progress_bar=False)
            except Exception: pass

    def _save(self) -> None:
        try:
            (self._index_path / "chunks.json").write_text(json.dumps(self._chunks))
            if self._embeddings is not None:
                import numpy as np; np.save(str(self._index_path / "embeddings.npy"), self._embeddings)
        except Exception as e: logger.debug("RAG save: %s", e)

    def _load_index(self) -> None:
        try:
            cf = self._index_path / "chunks.json"; ef = self._index_path / "embeddings.npy"
            if cf.exists():
                self._chunks = json.loads(cf.read_text()); self._bm25.build([c["text"] for c in self._chunks])
            if ef.exists():
                import numpy as np; self._embeddings = np.load(str(ef))
        except Exception: pass

    def stats(self) -> dict:
        return {"chunks": len(self._chunks), "sources": list({c["source"].split(os.sep)[-1] for c in self._chunks}),
                "encoder": self._encoder is not None, "bm25": len(self._bm25._docs) > 0}
