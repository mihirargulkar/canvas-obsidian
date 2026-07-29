#!/usr/bin/env python3
"""RAG chat over your own course materials, across ALL classes, with a
deterministic due-date path for deadline questions.

    python chat.py index                 # index every notes/<slug>/ course
    python chat.py ask "what's due this week"                 # -> deadlines (all classes)
    python chat.py ask "explain the bias-variance tradeoff"   # -> semantic + cites
    python chat.py ask "..." --course DS4400                  # restrict to one class

Embeds SOURCE note text (local sentence-transformers, no API quota). Each chunk
is tagged with its course so search can span all classes or filter to one.
"""
import argparse
import os
import re
import sys
from pathlib import Path

NOTES_ROOT = Path("notes")
DB = "chroma_db"
COLLECTION = "notes"
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


def chunks_from_note(text, source, course):
    """Split a note into (id, text, meta) chunks, one per '## section'."""
    parts = re.split(r"(?m)^(##\s+.+)$", text)
    out = []
    for i in range(1, len(parts), 2):
        section = parts[i].lstrip("# ").strip()
        body = parts[i + 1].strip()
        if len(body) < 20:
            continue
        out.append((f"{course}::{source}::{section[:50]}::{i}", f"{section}\n{body}",
                    {"source": source, "section": section, "course": course}))
    return out


def cmd_index(_=None):
    import chromadb
    client = chromadb.PersistentClient(path=DB)
    if COLLECTION in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION)     # rebuild from scratch, no drift
    col = _collection()
    ids, docs, metas = [], [], []
    courses = [d for d in sorted(NOTES_ROOT.glob("*")) if d.is_dir()]
    for cdir in courses:
        for p in sorted(cdir.glob("*.md")):
            for cid, text, meta in chunks_from_note(p.read_text(), p.stem, cdir.name):
                ids.append(cid); docs.append(text); metas.append(meta)
    if not ids:
        sys.exit("no notes to index — run ingest.py first")
    col.add(ids=ids, documents=docs, metadatas=metas)
    print(f"indexed {len(ids)} chunks from {len(courses)} course(s): "
          f"{', '.join(c.name for c in courses)}")


def route(query):
    return "deadline" if DATE_INTENT.search(query) else "semantic"


def answer_structured(query):
    import canvas
    from canvas import local_tz
    LOCAL_TZ = local_tz()
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
            "answer": f"Due in the next {days} days:\n\n" + "\n".join(lines), "sources": []}


def answer_semantic(query, k=5, course=None):
    from google import genai
    from dotenv import load_dotenv
    load_dotenv(str(Path(__file__).parent / ".env"))
    where = {"course": course} if course else None
    res = _collection().query(query_texts=[query], n_results=k, where=where)
    docs, metas = res["documents"][0], res["metadatas"][0]
    if not docs:
        return {"mode": "semantic",
                "answer": "No indexed material — run `python chat.py index`.", "sources": []}
    context = "\n\n".join(f"[{m['course']} · {m['source']} · {m['section']}]\n{d}"
                          for d, m in zip(docs, metas))
    prompt = (
        "Answer the student's question using ONLY the course material below. "
        "Cite the course, lecture and section in parentheses after each claim. If the "
        "answer is not in the material, say you don't find it in their notes.\n\n"
        f"MATERIAL:\n{context}\n\nQUESTION: {query}")
    try:
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        text = client.models.generate_content(model=ANSWER_MODEL, contents=prompt).text or ""
    except Exception as e:
        msg = str(e)
        friendly = ("The model is rate-limited right now — try again shortly."
                    if "429" in msg or "RESOURCE_EXHAUSTED" in msg else f"Model error: {msg[:120]}")
        return {"mode": "semantic", "answer": friendly, "sources": []}
    srcs = list(dict.fromkeys((m["course"], m["source"], m["section"]) for m in metas))
    return {"mode": "semantic", "answer": text,
            "sources": [{"course": c, "source": s, "section": sec} for c, s, sec in srcs]}


def answer(query, course=None):
    return answer_structured(query) if route(query) == "deadline" else answer_semantic(query, course=course)


def cmd_ask(a):
    res = answer(" ".join(a.query), course=a.course)
    tag = "deadline -> Canvas" if res["mode"] == "deadline" else "conceptual -> slides"
    print(f"[{tag}]\n\n{res['answer']}")
    if res["sources"]:
        print("\nsources:")
        for s in res["sources"]:
            print(f"  - {s['course']} · {s['source']} · {s['section']}")


def main():
    p = argparse.ArgumentParser(description="RAG chat across your course materials")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index", help="build/rebuild the vector index").set_defaults(func=cmd_index)
    pa = sub.add_parser("ask", help="ask a question")
    pa.add_argument("query", nargs="+")
    pa.add_argument("--course", default=None, help="restrict to one course slug")
    pa.set_defaults(func=cmd_ask)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
