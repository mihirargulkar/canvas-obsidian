#!/usr/bin/env python3
"""Phase 4: RAG chat over your own course materials, with a deterministic
due-date path for deadline questions.

    python chat.py index                 # build the vector index from notes/
    python chat.py ask "what's due this week"        # -> structured (Phase 1)
    python chat.py ask "explain the bias-variance tradeoff"   # -> semantic + cites

Embeds the SOURCE note text (not a re-paraphrase) so citations point at real
slides. Embeddings are local (sentence-transformers) — no API quota to index.
"""
import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

NOTES = Path("notes/DS4400")
DB = "chroma_db"
COLLECTION = "ds4400"
EMBED_MODEL = "all-MiniLM-L6-v2"
ANSWER_MODEL = os.getenv("ANSWER_MODEL", "gemini-3.5-flash")

DATE_INTENT = re.compile(
    r"\b(due|deadline|dues|overdue|upcoming|this week|next week|by when|"
    r"when.*(due|submit)|what.*due|homework.*due|assignments?\s+due)\b", re.I)


def _collection():
    import chromadb
    from chromadb.utils import embedding_functions
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
    client = chromadb.PersistentClient(path=DB)
    return client.get_or_create_collection(COLLECTION, embedding_function=ef)


def chunks_from_note(text, source):
    """Split a note into (id, text, meta) chunks, one per '## section'."""
    parts = re.split(r"(?m)^(##\s+.+)$", text)
    # parts = [pre, heading1, body1, heading2, body2, ...]
    out = []
    for i in range(1, len(parts), 2):
        section = parts[i].lstrip("# ").strip()
        body = parts[i + 1].strip()
        if len(body) < 20:
            continue
        chunk = f"{section}\n{body}"
        out.append((f"{source}::{section[:60]}::{i}", chunk,
                    {"source": source, "section": section}))
    return out


def cmd_index(_):
    col = _collection()
    # rebuild from scratch: drop and recreate (escape hatch — no incremental drift)
    import chromadb
    chromadb.PersistentClient(path=DB).delete_collection(COLLECTION)
    col = _collection()
    ids, docs, metas = [], [], []
    for p in sorted(NOTES.glob("*.md")):
        for cid, text, meta in chunks_from_note(p.read_text(), p.stem):
            ids.append(cid); docs.append(text); metas.append(meta)
    if not ids:
        sys.exit("no notes to index — run ingest.py first")
    col.add(ids=ids, documents=docs, metadatas=metas)
    print(f"indexed {len(ids)} chunks from {len(list(NOTES.glob('*.md')))} notes into {DB}/")


def route(query):
    return "deadline" if DATE_INTENT.search(query) else "semantic"


def answer_structured(query):
    import canvas
    from canvas import LOCAL_TZ
    days = 7
    m = re.search(r"(\d+)\s*day", query)
    if m: days = int(m.group(1))
    elif re.search(r"next week|two weeks|2 weeks", query, re.I): days = 14
    rows = canvas.upcoming(days)
    if not rows:
        return {"mode": "deadline", "answer": f"Nothing due in the next {days} days.", "sources": []}
    lines = [f"- **{n}** — {d.astimezone(LOCAL_TZ):%a %b %d, %I:%M %p}  ({c})"
             for d, c, n, _p in rows]
    return {"mode": "deadline",
            "answer": f"Due in the next {days} days:\n\n" + "\n".join(lines),
            "sources": []}


def answer_semantic(query, k=5):
    from google import genai
    from dotenv import load_dotenv
    load_dotenv(str(Path(__file__).parent / ".env"))
    col = _collection()
    res = col.query(query_texts=[query], n_results=k)
    docs, metas = res["documents"][0], res["metadatas"][0]
    if not docs:
        return {"mode": "semantic",
                "answer": "No indexed material yet — run `python chat.py index`.", "sources": []}
    context = "\n\n".join(f"[{m['source']} · {m['section']}]\n{d}" for d, m in zip(docs, metas))
    prompt = (
        "Answer the student's question using ONLY the course material below. "
        "Cite the lecture and section in parentheses after each claim. If the answer "
        "is not in the material, say you don't find it in their notes.\n\n"
        f"MATERIAL:\n{context}\n\nQUESTION: {query}")
    try:
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        r = client.models.generate_content(model=ANSWER_MODEL, contents=prompt)
        text = r.text or ""
    except Exception as e:
        msg = str(e)
        friendly = ("The model is rate-limited right now — try again shortly."
                    if "429" in msg or "RESOURCE_EXHAUSTED" in msg
                    else f"Model error: {msg[:120]}")
        return {"mode": "semantic", "answer": friendly, "sources": []}
    sources = list(dict.fromkeys((m["source"], m["section"]) for m in metas))
    return {"mode": "semantic", "answer": text,
            "sources": [{"source": s, "section": sec} for s, sec in sources]}


def answer(query):
    return answer_structured(query) if route(query) == "deadline" else answer_semantic(query)


def cmd_ask(a):
    res = answer(" ".join(a.query))
    tag = "deadline -> Canvas" if res["mode"] == "deadline" else "conceptual -> slides"
    print(f"[{tag}]\n\n{res['answer']}")
    if res["sources"]:
        print("\nsources:")
        for s in res["sources"]:
            print(f"  - {s['source']} · {s['section']}")


def main():
    p = argparse.ArgumentParser(description="RAG chat over your course materials (Phase 4)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index", help="build/rebuild the vector index").set_defaults(func=cmd_index)
    pa = sub.add_parser("ask", help="ask a question")
    pa.add_argument("query", nargs="+")
    pa.set_defaults(func=cmd_ask)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
