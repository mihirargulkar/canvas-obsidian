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
import json
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

GOLD_COURSE = "DS4400"

# NOT gemini-3.5-flash. That model's free tier is 20 requests PER DAY
# (quotaId GenerateRequestsPerDayPerProjectPerModel-FreeTier), and extract.py
# spends from the same budget on every sync, so a 14-request judging run could
# never finish. Three separate attempts died this way and the failure reads as
# a rate limit, not as "this eval cannot run on this model".
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "gemini-flash-latest")
JUDGE_BATCH = 50            # pairs per request; 350 pairs -> 7 calls
PASSAGE_CHARS = 400         # enough to judge topical relevance, keeps the call small

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


def load_gold(course, path):
    """Hand written pairs by default; the larger synthetic set if it exists.

    The hand written 10 are a smoke test — at that size a one rank shift moves
    MRR by 0.10, which is why no fusion weights were ever tuned against it.
    tools/make_eval_set.py generates a bigger set for actually comparing
    configurations.
    """
    if path and Path(path).exists():
        rows = json.loads(Path(path).read_text())
        return [(r["query"], r["source"]) for r in rows], f"{path} (synthetic)"
    return GOLD, "built-in (hand written)"


def judge(pairs):
    """Ask a model whether each retrieved passage answers the query.

    Exact-source matching is wrong for this corpus. A topic shows up in the
    lecture slides, the polls for that lecture, and a notebook, so retrieving
    "the Monte Carlo lecture" for a Monte Carlo question gets scored as a miss
    only because the query happened to be generated from the notebook. Judging
    relevance directly measures what we actually care about.
    """
    from google import genai
    from canvas_vault.canvas import gemini_key
    client = genai.Client(api_key=gemini_key())
    # A bare boolean array, not [{"n":..,"relevant":..}]: the object form spends
    # ~6x the output tokens and got truncated mid-array at 25 pairs, which
    # surfaced as a JSONDecodeError and cost the whole batch.
    prompt = (f"For each of the {len(pairs)} numbered pairs below, does the PASSAGE "
              "help answer the QUESTION for a student studying this course? Be "
              "strict about topical relevance but do not require it to be the "
              "single best passage.\n"
              f"Return ONLY a JSON array of exactly {len(pairs)} booleans, in order, "
              "one per pair. No other text.\n\n")
    body = "\n\n".join(f"[{i}] QUESTION: {q}\nPASSAGE: {d[:PASSAGE_CHARS]}"
                       for i, (q, d) in enumerate(pairs))
    for attempt in range(5):
        try:
            r = client.models.generate_content(
                model=JUDGE_MODEL, contents=[prompt, body],
                config={"response_mime_type": "application/json",
                        "max_output_tokens": 8192,
                        # This is a thinking model and it spent 800+ thought
                        # tokens on a 14-token prompt. On a 50-pair batch the
                        # thoughts consumed the whole output budget and the
                        # response came back with text=None. A binary relevance
                        # call does not need deliberation. (0 is rejected by the
                        # API for this model; 128 is the smallest that works.)
                        "thinking_config": {"thinking_budget": 128}})
            if r.text is None:
                raise ValueError(f"empty response ({r.candidates[0].finish_reason})")
            got = json.loads(r.text)
            # Positional answers mean a dropped item silently shifts every verdict
            # after it. Refuse the batch instead of scoring a misaligned one.
            if not isinstance(got, list) or len(got) != len(pairs):
                raise ValueError(f"judge returned {len(got)} verdicts for {len(pairs)} pairs")
            return {i: bool(v) for i, v in enumerate(got)}
        except Exception as e:
            msg = str(e)
            transient = isinstance(e, (json.JSONDecodeError, ValueError)) or any(
                c in msg for c in ("503", "429", "500", "UNAVAILABLE", "RESOURCE_EXHAUSTED"))
            if not transient or attempt == 4:
                raise
            wait = min(60, 4 * 2 ** attempt)
            print(f"    transient ({type(e).__name__}) retry in {wait}s")
            time.sleep(wait)


def evaluate_judged(course, k, gold):
    """recall@k and MRR using judged relevance instead of exact source match."""
    import canvas_vault.chat as chat
    col = chat._collection()
    hits = rr = 0
    batch, meta = [], []
    for question, _ in gold:
        res = col.query(query_texts=[question], n_results=k, where={"course": course})
        docs = res["documents"][0]
        for rank, d in enumerate(docs):
            batch.append((question, d)); meta.append((question, rank))
    verdicts, unjudged = {}, set()
    print(f"  judging {len(batch)} pairs in {math.ceil(len(batch)/JUDGE_BATCH)} "
          f"call(s) to {JUDGE_MODEL}")
    for start in range(0, len(batch), JUDGE_BATCH):
        try:
            got = judge(batch[start:start + JUDGE_BATCH])
        except Exception as e:
            # A failed judge call is missing data, not evidence of a miss. Counting
            # it as a miss makes a rate limit look like broken retrieval — which is
            # exactly the confusion this eval exists to prevent.
            print(f"  judge batch {start}: {type(e).__name__} {str(e)[:50]} (excluded)")
            unjudged.update(range(start, min(start + JUDGE_BATCH, len(batch))))
            got = {}
        for i, v in got.items():
            verdicts[start + i] = v
    per_query, incomplete = {}, set()
    for idx, (question, rank) in enumerate(meta):
        if idx in unjudged:
            incomplete.add(question)
        elif verdicts.get(idx):
            per_query.setdefault(question, rank + 1)
    scored = [q for q, _ in gold if q not in incomplete or q in per_query]
    for question in scored:
        if question in per_query:
            hits += 1; rr += 1 / per_query[question]
    if incomplete - set(per_query):
        print(f"  {len(incomplete - set(per_query))} quer(ies) excluded: judge "
              f"unavailable for their passages")
    n = len(scored)
    lo, hi = wilson(hits, n)
    print(f"{course}: JUDGED recall@{k} = {hits}/{n} ({hits/n:.0%}, 95% CI {lo:.0%}-{hi:.0%})"
          f"   MRR = {rr/n:.2f}")
    return hits, n


def wilson(hits, n, z=1.96):
    """95% interval for a proportion. Small samples deserve error bars: 10/10 on
    ten queries and 100/100 on a hundred are not the same claim."""
    if not n:
        return 0.0, 0.0
    p = hits / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def evaluate(course, k, gold=None, label="built-in"):
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

    n = len(gold)
    lo, hi = wilson(hits, n)
    print(f"{course}: recall@{k} = {hits}/{n} ({hits / n:.0%}, 95% CI {lo:.0%}-{hi:.0%})"
          f"   MRR = {rr / n:.2f}   [{label}]\n")
    for q, exp, rank, top in (rows if n <= 15 else []):
        mark = f"PASS rank {rank}" if rank == 1 else (f"ok   rank {rank}" if rank else "MISS      ")
        print(f"  {mark}  {q[:44]:44} want~{exp[:22]:22} got:{top[:28]}")
    return hits, n


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--course", default=GOLD_COURSE)
    p.add_argument("-k", type=int, default=5, help="top-k retrieved (default 5)")
    p.add_argument("--judged", action="store_true",
                   help="score with an LLM relevance judge instead of exact source match")
    p.add_argument("--gold", default="tools/eval_queries.json",
                   help="synthetic gold set to use if present")
    a = p.parse_args()
    if a.course != GOLD_COURSE:
        print(f"WARNING: gold set is written for {GOLD_COURSE}; results for "
              f"{a.course} are meaningless until you replace GOLD.\n")
    try:
        gold, label = load_gold(a.course, a.gold)
        if a.judged:
            hits, n = evaluate_judged(a.course, a.k, gold)
        else:
            hits, n = evaluate(a.course, a.k, gold, label)
    except Exception as e:
        sys.exit(f"retrieval eval failed ({type(e).__name__}) — is the index built? "
                 f"run: python -m canvas_vault.chat index")
    sys.exit(0 if hits == n else 1)     # non-zero so CI/scripts can gate on it


if __name__ == "__main__":
    main()
