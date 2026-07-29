#!/usr/bin/env python3
"""Phase 3: lecture notes -> concept nodes with [[wikilinks]] -> Obsidian vault.

    python extract.py            # extract all DS4400 lecture notes, build vault/

Two passes:
  1. per lecture note, Gemini extracts substantive concepts + intra-note links (JSON).
  2. merge concepts by canonical name ACROSS lectures (this is what creates
     cross-lecture links) and write one vault/concepts/<name>.md per concept.

Extraction JSON is cached per source-hash so re-runs cost 0 model calls.
"""
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path

from google import genai
from google.genai import types
from dotenv import load_dotenv

NOTES = Path("notes/DS4400")
VAULT = Path("vault")
XCACHE = Path("cache/concepts")               # per-note extraction JSON, keyed by note hash
MODEL = os.getenv("EXTRACT_MODEL", "gemini-3.5-flash")

# lecture decks only — skip polls (quiz noise) and HW solutions (exercises)
def is_lecture(p: Path) -> bool:
    n = p.name.lower()
    skip = ("polls", "solution", "announcement", "syllabus",    # not lecture concepts
            "assignment", "hw-", "code-")                       # homework + notebooks
    return p.suffix == ".md" and not any(s in n for s in skip)

PROMPT = """You are building a concept map for a Machine Learning course.
From the lecture note below, extract the SUBSTANTIVE technical concepts a student
would want as nodes in a concept map.

For each concept return:
- "name": canonical short noun phrase, Title Case (e.g. "Gradient Descent",
  "Mean Squared Error", "L2 Regularization"). Use the standard ML name, not the
  slide's phrasing, so the same idea gets the SAME name across lectures.
- "definition": 1-2 sentences grounded in this note.
- "related": names of OTHER concepts it directly depends on, is part of, or uses.

RELATED-LINK RULES (recall matters — a concept map is edges, not just nodes):
- Include the FOUNDATIONAL/prerequisite concepts a concept builds on, even if
  they were introduced in an earlier lecture (e.g. "Gradient Descent" builds on
  "Gradient" and "Derivative"; a regression method builds on its loss function).
- If a concept is a specific VARIANT of a more general one, link to the general
  concept (e.g. "L2 Regularization" -> "Regularization").

NAMING RULES (the SAME idea must get the SAME name across lectures):
- Use the concept's standard, widely-used ML name and keep it stable.
- Do NOT invent a synonym when the lecture already uses a standard term
  (use "Basis Functions", not a paraphrase like "Feature Map").

STRICT EXCLUSIONS:
- Only real domain concepts. EXCLUDE: agendas, "topics for today", instructor
  bio, course logistics, poll/exercise headers, named examples (e.g. "Netflix
  example"), and section-title filler.
- 5-15 concepts per lecture. Do not dump every heading.
Return ONLY a JSON array: [{"name":..,"definition":..,"related":[..]}]."""


def canon(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().casefold()


def safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "-", name).strip().strip(".") or "unnamed"


def _client():
    load_dotenv(str(Path(__file__).parent / ".env"))
    return genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def extract_note(client, text: str) -> list:
    """One Gemini call -> list of concept dicts, with backoff."""
    for attempt in range(6):
        try:
            r = client.models.generate_content(
                model=MODEL,
                contents=[PROMPT, "\n\nLECTURE NOTE:\n", text],
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            return json.loads(r.text)
        except Exception as e:
            msg = str(e)
            # bad JSON is retryable too — a re-generation usually returns valid JSON
            transient = isinstance(e, json.JSONDecodeError) or any(
                c in msg for c in ("503", "429", "500", "UNAVAILABLE", "RESOURCE_EXHAUSTED"))
            if not transient or attempt == 5:
                raise
            wait = min(60, 4 * 2 ** attempt)
            print(f"      transient ({msg[:40]}...) retry in {wait}s")
            time.sleep(wait)


def pass1():
    """Extract concepts per lecture note (cached by note hash). Returns
    {lecture_stem: [concept dicts]}."""
    import hashlib
    client = _client()
    XCACHE.mkdir(parents=True, exist_ok=True)
    out = {}
    notes = sorted(p for p in NOTES.glob("*.md") if is_lecture(p))
    print(f"pass 1: extracting concepts from {len(notes)} lecture note(s)\n")
    for p in notes:
        text = p.read_text()
        h = hashlib.sha256(text.encode()).hexdigest()
        cache = XCACHE / f"{h}.json"
        if cache.exists():
            out[p.stem] = json.loads(cache.read_text())
            print(f"  cached  {p.name} ({len(out[p.stem])} concepts)")
            continue
        try:
            concepts = extract_note(client, text)
            cache.write_text(json.dumps(concepts, indent=2))
            out[p.stem] = concepts
            print(f"  new     {p.name} ({len(concepts)} concepts)")
        except Exception as e:
            print(f"          FAILED {p.name}: {str(e)[:70]} (retry next run)")
    return out


def pass2(per_lecture: dict):
    """Merge concepts across lectures by canonical name, write the vault."""
    nodes = {}  # canon -> {"name","definition","related":set,"lectures":set}
    for stem, concepts in per_lecture.items():
        for c in concepts:
            k = canon(c["name"])
            if k not in nodes:
                nodes[k] = {"name": c["name"].strip(), "definition": c.get("definition", ""),
                            "related": set(), "lectures": set()}
            nodes[k]["lectures"].add(stem)
            for r in c.get("related", []):
                nodes[k]["related"].add(canon(r))

    (VAULT / "concepts").mkdir(parents=True, exist_ok=True)
    (VAULT / "lectures").mkdir(parents=True, exist_ok=True)
    # copy lecture notes in so [[Lecture...]] links resolve in Obsidian
    for p in NOTES.glob("*.md"):
        if is_lecture(p):
            (VAULT / "lectures" / p.name).write_text(p.read_text())

    edges = 0
    for k, n in nodes.items():
        # keep only links to concepts that actually exist as nodes (no ghost hairball)
        # ponytail: exact canonical match; add alias/fuzzy merge if recall proves low
        links = sorted({nodes[r]["name"] for r in n["related"] if r in nodes and r != k})
        edges += len(links)
        fm = ["---", f"lectures: [{', '.join(sorted(n['lectures']))}]", "---", ""]
        body = [f"# {n['name']}", "", n["definition"], ""]
        if links:
            body += ["## Related", *[f"- [[{l}]]" for l in links], ""]
        body += ["## Appears in", *[f"- [[{s}]]" for s in sorted(n["lectures"])], ""]
        (VAULT / "concepts" / f"{safe_filename(n['name'])}.md").write_text("\n".join(fm + body))

    print(f"\npass 2: {len(nodes)} concept nodes, {edges} concept-concept links -> {VAULT}/")
    return nodes


def _parse_concept(path):
    t = path.read_text()
    name = re.search(r"^#\s+(.+)$", t, re.M)
    name = name.group(1).strip() if name else path.stem
    lects = re.search(r"lectures: \[(.*?)\]", t)
    lects = [x for x in (lects.group(1).split(", ") if lects else []) if x]
    defn = ""
    body = t.split("---", 2)[-1]
    for line in body.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "-", "lectures")):
            defn = line; break
    links = re.findall(r"\[\[(.+?)\]\]", t.split("## Related")[1].split("## Appears in")[0]) \
        if "## Related" in t else []
    return name, {"lects": lects, "definition": defn, "links": links}

def graph_data():
    files = list((VAULT / "concepts").glob("*.md"))
    parsed = dict(_parse_concept(f) for f in files)
    deg = {}
    edges = []
    seen = set()
    for name, d in parsed.items():
        for l in d["links"]:
            if l in parsed:
                key = frozenset((name, l))
                if key not in seen:
                    seen.add(key); edges.append({"s": name, "t": l})
                    deg[name] = deg.get(name, 0) + 1; deg[l] = deg.get(l, 0) + 1
    nodes = [{"id": n, "lect": (d["lects"] or ["?"])[0], "degree": deg.get(n, 0)}
             for n, d in parsed.items()]
    return {"nodes": nodes, "edges": edges}

def concept_data(name):
    f = VAULT / "concepts" / f"{safe_filename(name)}.md"
    if not f.exists():
        return None
    n, d = _parse_concept(f)
    return {"name": n, "definition": d["definition"],
            "links": [l for l in d["links"]], "lectures": d["lects"]}

def main():
    per = pass1()
    if per:
        pass2(per)


if __name__ == "__main__":
    main()
