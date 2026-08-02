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
import sqlite3
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id   TEXT PRIMARY KEY,
    doc  TEXT NOT NULL,
    meta TEXT NOT NULL,
    vec  BLOB NOT NULL
);
"""


class VectorStore:
    """Persistent store of (id, document, metadata, embedding)."""

    def __init__(self, path, embed):
        """`embed` takes a list of strings and returns an (n, dim) float array."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._embed = embed
        self.db = sqlite3.connect(self.path)
        self.db.execute(SCHEMA)
        self.db.commit()

    # --- writes ----------------------------------------------------------

    def upsert(self, ids, documents, metadatas):
        vecs = self._encode(documents)
        self.db.executemany(
            "INSERT INTO chunks (id, doc, meta, vec) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET doc=excluded.doc, meta=excluded.meta, "
            "vec=excluded.vec",
            [(i, d, json.dumps(m), v.astype(np.float32).tobytes())
             for i, d, m, v in zip(ids, documents, metadatas, vecs)])
        self.db.commit()

    def delete(self, ids):
        self.db.executemany("DELETE FROM chunks WHERE id = ?", [(i,) for i in ids])
        self.db.commit()

    # --- reads -----------------------------------------------------------

    def get(self):
        """Every id and document, for the incremental-index diff."""
        rows = self.db.execute("SELECT id, doc FROM chunks").fetchall()
        return {"ids": [r[0] for r in rows], "documents": [r[1] for r in rows]}

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def query(self, query_texts, n_results=5, where=None):
        """Nearest chunks to each query. Shapes match what the callers expect:
        {"documents": [[...]], "metadatas": [[...]]}, one list per query."""
        sql, params = "SELECT doc, meta, vec FROM chunks", []
        if where:                       # only ever an equality filter on metadata
            (field, value), = where.items()
            sql += " WHERE json_extract(meta, ?) = ?"
            params = [f"$.{field}", value]
        rows = self.db.execute(sql, params).fetchall()
        if not rows:
            return {"documents": [[] for _ in query_texts],
                    "metadatas": [[] for _ in query_texts]}

        mat = np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float32)
        mat = mat.reshape(len(rows), -1)
        docs, metas = [], []
        for q in self._encode(query_texts):
            best = np.argsort(-(mat @ q))[:n_results]
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
