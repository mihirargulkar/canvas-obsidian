#!/usr/bin/env python3
"""DEVELOPMENT TOOL — measure whether retrieval surfaces the right source.

    python eval_retrieval.py [--course SLUG] [-k 5]

The concept graph has eval_graph.py; this is the equivalent for the RAG half.
Without it, "retrieval seems fine" is a vibe: a chunker change or a different
embedding model can quietly degrade recall and nothing would catch it.

Each case is a realistic student question plus the note that SHOULD be retrieved
(a substring of the source filename). Scores:
  recall@k  — the expected source appears anywhere in the top k
  MRR       — 1/rank of the first correct hit, so rank 1 beats rank 5

NOTE: like eval_graph.py, the gold set below is written for ONE course. Replace
GOLD with questions about your own material to evaluate your own corpus.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

GOLD_COURSE = "DS4400"

# (question a student would actually ask, substring of the source that answers it)
GOLD = [
    ("what topics are on the midterm 2 study guide",      "announcements"),
    ("when are the exams scheduled",                      "announcements"),
    ("what is the late homework policy",                  "syllabus"),
    ("what does homework 3 ask me to do",                 "hw-Homework#3"),
    ("explain the bias variance tradeoff",                "Lecture8"),
    ("how does gradient descent update the weights",      "Lecture4"),
    ("what is L2 regularization and why use it",          "Lecture4"),
    ("show the notebook implementing gradient descent epochs", "code-gradient_descent"),
    ("numpy examples for vector norms",                   "code-norms"),
    ("what is a probability density function",            "Lecture9"),
]


def evaluate(course, k):
    import canvas_vault.chat as chat
    col = chat._collection()
    hits, rr, rows = 0, 0.0, []
    for question, expected in GOLD:
        res = col.query(query_texts=[question], n_results=k,
                        where={"course": course})
        sources = [m["source"] for m in res["metadatas"][0]]
        rank = next((i + 1 for i, s in enumerate(sources)
                     if expected.lower() in s.lower()), None)
        if rank:
            hits += 1
            rr += 1 / rank
        rows.append((question, expected, rank, sources[0] if sources else "—"))

    n = len(GOLD)
    print(f"{course}: recall@{k} = {hits}/{n} ({hits / n:.0%})   MRR = {rr / n:.2f}\n")
    for q, exp, rank, top in rows:
        mark = f"PASS rank {rank}" if rank == 1 else (f"ok   rank {rank}" if rank else "MISS      ")
        print(f"  {mark}  {q[:44]:44} want~{exp[:22]:22} got:{top[:28]}")
    return hits, n


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--course", default=GOLD_COURSE)
    p.add_argument("-k", type=int, default=5, help="top-k retrieved (default 5)")
    a = p.parse_args()
    if a.course != GOLD_COURSE:
        print(f"WARNING: gold set is written for {GOLD_COURSE}; results for "
              f"{a.course} are meaningless until you replace GOLD.\n")
    try:
        hits, n = evaluate(a.course, a.k)
    except Exception as e:
        sys.exit(f"retrieval eval failed ({type(e).__name__}) — is the index built? "
                 f"run: python -m canvas_vault.chat index")
    sys.exit(0 if hits == n else 1)     # non-zero so CI/scripts can gate on it


if __name__ == "__main__":
    main()
