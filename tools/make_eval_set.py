#!/usr/bin/env python3
"""DEVELOPMENT TOOL — generate a synthetic retrieval gold set.

    python tools/make_eval_set.py --course DS4400 --n 100

Writes tools/eval_queries.json: [{"query": ..., "source": ..., "section": ...}].

Why synthetic, and what it costs
--------------------------------
A hand written gold set of 10 queries is a smoke test, not an instrument: the
gap between MRR 0.85 and 0.90 is one query moving one rank. Real query logs
would be better, but there aren't any, so we generate.

The failure mode to avoid is vocabulary leakage. If you show a model a chunk and
ask "what question does this answer?", the question comes back wearing the
chunk's own words, and retrieval (BM25 especially) scores far higher than it
would on questions a student actually types. The generation prompt therefore
demands paraphrase, and every candidate is filtered on how much of its
vocabulary already appears in the target chunk. Anything too close is discarded.

Even so: treat these numbers as useful for COMPARING configurations, not as an
absolute measure of quality. Synthetic queries are easier than real ones.
"""
import argparse
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from canvas_vault import chat  # noqa: E402
from canvas_vault.canvas import gemini_key  # noqa: E402

MODEL = "gemini-3.5-flash"
OUT = Path("tools/eval_queries.json")

PROMPT = """You are writing evaluation queries for a student's course search tool.

Below are numbered excerpts from one course's lecture notes. For each excerpt,
write ONE question a student would plausibly type into a search box.

Critical rules:
- Write the question a student asks BEFORE they know the answer, when they are
  confused and half remember a topic. Not a quiz question about the text.
- Do NOT reuse the excerpt's distinctive wording. Paraphrase. If the excerpt says
  "conditional entropy", a student might type "how does knowing X change the
  uncertainty in Y".
- Vary the style: some short and keyword-ish ("kl divergence formula"), some
  full sentences, some vague ("the thing about splitting nodes").
- No question should name the lecture number or the file.

Return ONLY a JSON array of objects: [{"n": <excerpt number>, "query": "..."}]
"""


def leakage(query, chunk):
    """Fraction of the query's content words that appear verbatim in the chunk.

    High overlap means the query was copied out of the text rather than being
    the kind of thing a student would type, which makes retrieval look easier
    than it is.
    """
    stop = {"the", "a", "an", "is", "are", "was", "of", "to", "in", "for", "on",
            "how", "what", "why", "does", "do", "did", "and", "or", "it", "this",
            "that", "with", "we", "i", "my", "you", "be", "can", "if", "when"}
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in stop]
    if not words:
        return 1.0
    body = set(re.findall(r"[a-z0-9]+", chunk.lower()))
    return sum(w in body for w in words) / len(words)


def main():
    p = argparse.ArgumentParser(description="Generate a synthetic retrieval gold set")
    p.add_argument("--course", default="DS4400")
    p.add_argument("--n", type=int, default=100, help="target number of queries")
    p.add_argument("--max-leak", type=float, default=0.6,
                   help="discard queries whose words are this fraction copied from the chunk")
    p.add_argument("--seed", type=int, default=11)
    a = p.parse_args()

    ids, docs, metas, _ = chat._collect_chunks()
    pool = [(d, m) for d, m in zip(docs, metas)
            if m["course"] == a.course and len(d) > 400]
    if not pool:
        sys.exit(f"no chunks for {a.course} — build the index first")
    random.seed(a.seed)
    random.shuffle(pool)
    pool = pool[:int(a.n * 1.6)]        # over-sample; filtering will drop some
    print(f"{len(pool)} candidate chunks from {a.course}")

    from google import genai
    client = genai.Client(api_key=gemini_key())

    out, batch = [], 12
    for start in range(0, len(pool), batch):
        group = pool[start:start + batch]
        listing = "\n\n".join(f"[{i}] {d[:1200]}" for i, (d, _) in enumerate(group))
        try:
            r = client.models.generate_content(
                model=MODEL, contents=[PROMPT, listing],
                config={"response_mime_type": "application/json"})
            items = json.loads(r.text)
        except Exception as e:
            print(f"  batch {start}: {type(e).__name__} {str(e)[:60]} — skipped")
            continue
        for item in items:
            i = item.get("n")
            if not isinstance(i, int) or not 0 <= i < len(group):
                continue
            doc, meta = group[i]
            q = (item.get("query") or "").strip()
            leak = leakage(q, doc)
            if len(q) < 8 or leak > a.max_leak:
                continue
            out.append({"query": q, "source": meta["source"],
                        "section": meta["section"], "leakage": round(leak, 2)})
        print(f"  {len(out)} kept after {start + len(group)} chunks")
        if len(out) >= a.n:
            break

    out = out[:a.n]
    OUT.write_text(json.dumps(out, indent=1))
    avg = sum(o["leakage"] for o in out) / max(len(out), 1)
    print(f"\nwrote {OUT}: {len(out)} queries, mean vocabulary overlap {avg:.2f}")
    print("Synthetic. Good for comparing configurations, optimistic in absolute terms.")


if __name__ == "__main__":
    main()
