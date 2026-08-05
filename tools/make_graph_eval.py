#!/usr/bin/env python3
"""DEVELOPMENT TOOL — generate a concept-pair gold set for eval_graph.py.

    python tools/make_graph_eval.py --course DS4400 --n 120

Writes tools/graph_pairs.json: [{"a":.., "b":.., "why":.., "lecture":..}].

Why this is generated from the NOTES and not from the graph
-----------------------------------------------------------
The obvious way to grow this set is to look at the graph and write down pairs it
already links. Every one of them would pass, which measures nothing.

So the pairs come from the lecture text, and the model producing them is never
shown the graph, the node list, or any edge. A generated pair may well name a
concept that was never extracted as a node. That is not a bug in the gold set,
it is the node-recall failure the hand written set caught once already, when
"Learning Rate" quietly stopped being extracted.

What this still cannot rule out
-------------------------------
The same model family writes the pairs and extracts the concepts, so shared
vocabulary and shared blind spots inflate the score. Two guards: pairs are
grounded in the note's own wording, and eval_graph.py reports a random-pair
baseline. A score is only meaningful as lift over that baseline, because a dense
enough graph connects everything within 2 hops and scores well for free.
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from canvas_vault import extract  # noqa: E402
from canvas_vault.canvas import gemini_key  # noqa: E402

MODEL = "gemini-flash-latest"
OUT = Path("tools/graph_pairs.json")

PROMPT = """You are checking whether a student's concept map for this course is
any good. Below is one lecture note.

Name pairs of concepts from THIS note that a student revising should see
directly connected on a concept map, because one is a prerequisite for the
other, one is a specific variant of the other, or one is a component the other
is built out of.

Rules:
- Use the note's own vocabulary for the concept names. Do not substitute the
  term you would prefer.
- Only substantive course concepts. Never agendas, logistics, section headers,
  worked examples or the instructor.
- Pick pairs where the connection is real and a marker would expect it, not
  pairs that merely appear on the same slide.
- "why" must be under 12 words.
- 4 to 8 pairs. Fewer is fine if the note is thin.

Return ONLY a JSON array: [{"a":"...","b":"...","why":"..."}]
"""


def generate(client, text):
    for attempt in range(5):
        try:
            r = client.models.generate_content(
                model=MODEL, contents=[PROMPT, "\n\nLECTURE NOTE:\n", text[:14000]],
                config={"response_mime_type": "application/json",
                        "max_output_tokens": 4096,
                        "thinking_config": {"thinking_budget": 128}})
            if r.text is None:
                raise ValueError(f"empty ({r.candidates[0].finish_reason})")
            got = json.loads(r.text)
            if not isinstance(got, list):
                raise ValueError("not a list")
            return got
        except Exception as e:
            if attempt == 4:
                raise
            time.sleep(min(60, 4 * 2 ** attempt))


def main():
    p = argparse.ArgumentParser(description="Generate a concept-pair gold set")
    p.add_argument("--course", default="DS4400")
    p.add_argument("--n", type=int, default=120)
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args()

    notes = sorted(q for q in extract.notes_dir(a.course).glob("*.md")
                   if extract.is_lecture(q))
    if not notes:
        sys.exit(f"no lecture notes for {a.course}")
    random.seed(a.seed)
    print(f"{len(notes)} lecture note(s) in {a.course}")

    from google import genai
    client = genai.Client(api_key=gemini_key())

    pairs, seen = [], set()
    for note in notes:
        try:
            got = generate(client, note.read_text())
        except Exception as e:
            print(f"  {note.stem[:34]:34} {type(e).__name__} — skipped")
            continue
        kept = 0
        for item in got:
            x, y = (item.get("a") or "").strip(), (item.get("b") or "").strip()
            if not x or not y or extract.canon(x) == extract.canon(y):
                continue
            key = frozenset((extract.canon(x), extract.canon(y)))
            if key in seen:
                continue          # the same pair from two lectures is one test case
            seen.add(key)
            pairs.append({"a": x, "b": y, "why": (item.get("why") or "").strip(),
                          "lecture": note.stem})
            kept += 1
        print(f"  {note.stem[:34]:34} +{kept:<2} (total {len(pairs)})")
        if len(pairs) >= a.n:
            break

    pairs = pairs[:a.n]
    OUT.write_text(json.dumps(pairs, indent=1))
    print(f"\nwrote {OUT}: {len(pairs)} pairs from {len({p['lecture'] for p in pairs})} lectures")
    print("Generated from the notes, never from the graph. Score with eval_graph.py,")
    print("and read it as lift over the random-pair baseline, not as a raw percentage.")


if __name__ == "__main__":
    main()
