#!/usr/bin/env python3
"""RAG chat over your own course materials, across ALL classes, with a
deterministic due-date path for deadline questions.

    python -m canvas_vault.chat index                 # index every notes/<slug>/ course
    python -m canvas_vault.chat ask "what's due this week"                 # -> deadlines (all classes)
    python -m canvas_vault.chat ask "explain the bias-variance tradeoff"   # -> semantic + cites
    python -m canvas_vault.chat ask "..." --course DS4400                  # restrict to one class

Embeds SOURCE note text (local sentence-transformers, no API quota). Each chunk
is tagged with its course so search can span all classes or filter to one.
"""
import argparse
import os
import re
import sys
from pathlib import Path

from . import chdir_root
from . import ROOT

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
    """Split a note into (id, text, meta) chunks, one per '## section'.

    parts[0] is whatever precedes the first '## ' heading, and it used to be
    dropped. A note written with only '#' headings then produced ZERO chunks and
    was invisible to search with no warning — the vision prompt asks for headings
    "mirroring the slides", so `#`-only transcriptions happen regularly.
    """
    parts = re.split(r"(?m)^(##\s+.+)$", text)
    out = []

    def add(section, body, i):
        body = body.strip()
        if len(body) < 20:
            return
        out.append((f"{course}::{source}::{section[:50]}::{i}", f"{section}\n{body}",
                    {"source": source, "section": section, "course": course}))

    # preamble: strip YAML frontmatter, then index whatever is left
    pre = parts[0]
    if pre.startswith("---"):
        pre = pre.split("---", 2)[-1]
    add(source, re.sub(r"(?m)^#\s+", "", pre), 0)

    for i in range(1, len(parts), 2):
        add(parts[i].lstrip("# ").strip(), parts[i + 1], i)
    return out


def _collect_chunks():
    """All chunks across every course: (ids, docs, metas, course_names)."""
    ids, docs, metas = [], [], []
    courses = [d for d in sorted(NOTES_ROOT.glob("*")) if d.is_dir()]
    for cdir in courses:
        for p in sorted(cdir.glob("*.md")):
            for cid, text, meta in chunks_from_note(p.read_text(), p.stem, cdir.name):
                ids.append(cid); docs.append(text); metas.append(meta)
    return ids, docs, metas, [c.name for c in courses]


def index(rebuild=False, quiet=False):
    """Sync the vector index with notes/ and return the number of chunks changed.

    Incremental by default: embedding 1,200+ unchanged chunks takes ~18s, which is
    pure waste on a daily sync where nothing moved. Only added/changed/removed
    chunks are touched. `rebuild=True` forces a full re-embed (escape hatch if the
    chunker or embedding model changes).
    """
    import chromadb
    client = chromadb.PersistentClient(path=DB)
    if rebuild and COLLECTION in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION)
    col = _collection()

    ids, docs, metas, courses = _collect_chunks()
    if not ids:
        # Not sys.exit: SystemExit isn't an Exception, so it sails through
        # course.sync's handler and out of the MCP tool that called us.
        if not quiet:
            print("index: nothing to index yet — run the sync first")
        return 0

    existing = col.get(include=["documents"])
    have = dict(zip(existing["ids"], existing["documents"]))
    want = dict(zip(ids, docs))

    new = [i for i in ids if i not in have]
    changed = [i for i in ids if i in have and have[i] != want[i]]
    gone = [i for i in have if i not in want]

    if gone:
        col.delete(ids=gone)
    upsert = new + changed
    if upsert:
        pos = {i: n for n, i in enumerate(ids)}
        col.upsert(ids=upsert,
                   documents=[docs[pos[i]] for i in upsert],
                   metadatas=[metas[pos[i]] for i in upsert])

    if not quiet:
        if upsert or gone:
            print(f"index: +{len(new)} new, ~{len(changed)} changed, -{len(gone)} removed "
                  f"({len(ids)} chunks, {len(courses)} course(s))")
        else:
            print(f"index: up to date ({len(ids)} chunks, {len(courses)} course(s))")
    return len(new) + len(changed) + len(gone)


def cmd_index(a=None):
    index(rebuild=getattr(a, "rebuild", False))


def route(query):
    return "deadline" if DATE_INTENT.search(query) else "semantic"


def answer_structured(query):
    from . import canvas
    from .canvas import local_tz
    LOCAL_TZ = local_tz()
    # "overdue"/"late"/"missed" ask about the PAST. upcoming() only looks forward,
    # so these used to be answered "nothing due in the next 7 days" — the opposite
    # of the truth. Answer them with a negative window instead.
    if re.search(r"\b(overdue|past due|late|missed)\b", query, re.I):
        days, back = 0, 14
        rows = canvas.overdue(back)
        if not rows:
            return {"mode": "deadline",
                    "answer": f"Nothing overdue in the last {back} days.", "sources": []}
        lines = [f"- **{n}** — was due {d.astimezone(LOCAL_TZ):%a %b %d, %I:%M %p}  ({c})"
                 for d, c, n, _p in rows]
        return {"mode": "deadline",
                "answer": f"Overdue in the last {back} days:\n\n" + "\n".join(lines),
                "sources": []}

    days = 7
    m = re.search(r"(\d+)\s*days?\b", query)
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
    load_dotenv(ROOT / ".env")
    where = {"course": course} if course else None
    res = _collection().query(query_texts=[query], n_results=k, where=where)
    docs, metas = res["documents"][0], res["metadatas"][0]
    if not docs:
        return {"mode": "semantic",
                "answer": "No indexed material — run `python -m canvas_vault.chat index`.", "sources": []}
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
    chdir_root()      # data paths are relative to the repo root
    p = argparse.ArgumentParser(description="RAG chat across your course materials")
    sub = p.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("index", help="sync the vector index with notes/ (incremental)")
    pi.add_argument("--rebuild", action="store_true",
                    help="force a full re-embed (use if the chunker or model changed)")
    pi.set_defaults(func=cmd_index)
    pa = sub.add_parser("ask", help="ask a question")
    pa.add_argument("query", nargs="+")
    pa.add_argument("--course", default=None, help="restrict to one course slug")
    pa.set_defaults(func=cmd_ask)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
