"""A tiny vector store: SQLite for the rows, numpy for the search.

ChromaDB used to do this job. It is built to scale to millions of vectors, and
pays for that with kubernetes, grpc and onnxruntime in the dependency tree. This
corpus is about 1,300 chunks, roughly 2 MB of float32, so the "index" is a
brute-force dot product over a matrix that fits comfortably in L2 cache.

Vectors are stored L2-normalised, so cosine similarity is just a dot product.
The method names mirror the subset of Chroma's API the callers used, which keeps
the swap to a handful of lines elsewhere.

ponytail: brute force is O(n) per query and fine to ~100k chunks (tens of ms).
If a vault ever gets bigger than that, add an ANN index; don't add a server.
"""
import json
import math
import re
import sqlite3
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id   TEXT PRIMARY KEY,
    doc  TEXT NOT NULL,
    meta TEXT NOT NULL,
    vec  BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text):
    return _WORD.findall(text.lower())


def bm25_scores(query, docs, k1=1.5, b=0.75):
    """Classic BM25 over an in-memory list of documents.

    Embeddings are weak on exact terms ("L2 regularization", "homework 3");
    BM25 is good at precisely that, and bad at paraphrase, which embeddings
    handle. Fusing the two beats either alone. It's ~25 lines and needs nothing
    that isn't already installed.
    """
    toks = [_tokens(d) for d in docs]
    n = len(toks)
    if not n:
        return np.zeros(0)
    avg = sum(map(len, toks)) / n
    freqs = [Counter(t) for t in toks]
    df = Counter(w for t in toks for w in set(t))
    scores = np.zeros(n)
    for w in set(_tokens(query)):
        if w not in df:
            continue
        idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
        for i, tf in enumerate(freqs):
            f = tf.get(w, 0)
            if f:
                scores[i] += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(toks[i]) / avg))
    return scores


def rrf(*rankings, k=60):
    """Reciprocal rank fusion: combine rankings without needing their scores to
    be on comparable scales. Deliberately untuned — the gold set is 10 queries,
    so fitting weights to it would be overfitting, not improvement."""
    fused = Counter()
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            fused[idx] += 1 / (k + rank + 1)
    return [i for i, _ in fused.most_common()]


class VectorStore:
    """Persistent store of (id, document, metadata, embedding)."""

    def __init__(self, path, embed, embedder_id="default"):
        """`embed` maps a list of strings to an (n, dim) array.

        `embedder_id` names the model that produced the stored vectors. Vectors
        from two different models aren't comparable, so if it changes (someone
        installs sentence-transformers next to the static default) the index is
        dropped and rebuilt rather than silently mixing incompatible embeddings.
        """
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._embed = embed
        with self.connect() as db:
            db.executescript(SCHEMA)
            row = db.execute("SELECT v FROM meta WHERE k='embedder'").fetchone()
            if row and row[0] != embedder_id:
                db.execute("DELETE FROM chunks")
            db.execute("INSERT INTO meta (k, v) VALUES ('embedder', ?) "
                       "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (embedder_id,))
            db.commit()

    @contextmanager
    def connect(self):
        """A fresh connection per operation, not one cached for the process life.

        A cached handle broke two ways at once under MCP. The server answers
        tool calls on a thread pool, and sqlite3 refuses a connection used off
        the thread that made it ("SQLite objects created in a thread can only be
        used in that same thread"), which is what made search fail. And a server
        that lives for days holds its handle across every reindex.

        Connecting costs microseconds next to embedding a query, so there is no
        reason to keep one. timeout lets a concurrent writer finish instead of
        failing instantly; the daily sync and an MCP refresh do overlap.
        """
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute("PRAGMA journal_mode=WAL")   # readers don't block a writer
            yield db
        finally:
            db.close()

    @property
    def db(self):
        raise AttributeError(
            "VectorStore.db was a cached connection and is gone: it broke across "
            "threads under MCP and went stale across reindexes. Use `with "
            "store.connect() as db:` instead.")

    # --- writes ----------------------------------------------------------

    def upsert(self, ids, documents, metadatas):
        vecs = self._encode(documents)
        with self.connect() as db:
            db.executemany(
            "INSERT INTO chunks (id, doc, meta, vec) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET doc=excluded.doc, meta=excluded.meta, "
            "vec=excluded.vec",
                [(i, d, json.dumps(m), v.astype(np.float32).tobytes())
                 for i, d, m, v in zip(ids, documents, metadatas, vecs)])
            db.commit()

    def delete(self, ids):
        with self.connect() as db:
            db.executemany("DELETE FROM chunks WHERE id = ?", [(i,) for i in ids])
            db.commit()

    # --- reads -----------------------------------------------------------

    def get(self):
        """Every id and document, for the incremental-index diff."""
        with self.connect() as db:
            rows = db.execute("SELECT id, doc FROM chunks").fetchall()
        return {"ids": [r[0] for r in rows], "documents": [r[1] for r in rows]}

    def count(self):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def query(self, query_texts, n_results=5, where=None):
        """Nearest chunks to each query. Shapes match what the callers expect:
        {"documents": [[...]], "metadatas": [[...]]}, one list per query."""
        sql, params = "SELECT doc, meta, vec FROM chunks", []
        if where:                       # only ever an equality filter on metadata
            (field, value), = where.items()
            sql += " WHERE json_extract(meta, ?) = ?"
            params = [f"$.{field}", value]
        with self.connect() as db:
            rows = db.execute(sql, params).fetchall()
        if not rows:
            return {"documents": [[] for _ in query_texts],
                    "metadatas": [[] for _ in query_texts]}

        mat = np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float32)
        mat = mat.reshape(len(rows), -1)
        texts = [r[0] for r in rows]
        docs, metas = [], []
        for text, q in zip(query_texts, self._encode(query_texts)):
            dense = np.argsort(-(mat @ q))                    # semantic
            keyword = np.argsort(-bm25_scores(text, texts))   # exact terms
            best = rrf(dense, keyword)[:n_results]
            docs.append([rows[i][0] for i in best])
            metas.append([json.loads(rows[i][1]) for i in best])
        return {"documents": docs, "metadatas": metas}

    # --- internals -------------------------------------------------------

    def _encode(self, texts):
        v = np.asarray(self._embed(list(texts)), dtype=np.float32)
        if v.ndim == 1:
            v = v.reshape(1, -1)
        norms = np.linalg.norm(v, axis=1, keepdims=True)
        return v / np.where(norms == 0, 1, norms)      # store unit vectors
