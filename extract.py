#!/usr/bin/env python3
"""Phase 3: lecture notes -> concept nodes with [[wikilinks]] -> per-course vault.

    python extract.py DS4400      # build vault/DS4400/ from notes/DS4400/

Two passes: (1) per lecture note, Gemini extracts concepts + intra-note links;
(2) merge concepts by canonical name ACROSS lectures (creates cross-lecture
links) into vault/<slug>/concepts/. Extraction JSON is cached per note-hash.
"""
import argparse
import json
import os
import re
import time
from pathlib import Path

from google import genai
from google.genai import types
from dotenv import load_dotenv

XCACHE = Path("cache/concepts")               # per-note extraction JSON, keyed by note hash
MODEL = os.getenv("EXTRACT_MODEL", "gemini-3.5-flash")


def notes_dir(slug): return Path("notes") / slug
def vault_dir(slug): return Path("vault") / slug


def is_lecture(p: Path) -> bool:
    """Lecture decks only — skip polls, solutions, announcements, syllabus,
    homework (hw-), and notebooks (code-): none are lecture concepts."""
    n = p.name.lower()
    skip = ("polls", "solution", "announcement", "syllabus", "assignment", "hw-", "code-")
    return p.suffix == ".md" and not any(s in n for s in skip)


PROMPT = """You are building a concept map for a university course.
From the lecture note below, extract the SUBSTANTIVE concepts a student would
want as nodes in a concept map.

For each concept return:
- "name": canonical short noun phrase, Title Case. Use the standard term for the
  field, not the slide's phrasing, so the same idea gets the SAME name across lectures.
- "definition": 1-2 sentences grounded in this note.
- "related": names of OTHER concepts it directly depends on, is part of, or uses.

RELATED-LINK RULES (recall matters — a concept map is edges, not just nodes):
- Include the FOUNDATIONAL/prerequisite concepts a concept builds on, even if
  introduced in an earlier lecture (illustrative: "Gradient Descent" builds on
  "Gradient" and "Derivative"; a method builds on its underlying assumption).
- If a concept is a specific VARIANT of a more general one, link to the general
  concept (illustrative: "L2 Regularization" -> "Regularization").
- The examples above show the SHAPE of a link, not the subject matter — extract
  whatever this course is actually about, in its own vocabulary.

  (Tried and reverted: adding psychology/literature examples alongside these to
  reduce domain bias measurably HURT link recall on the evaluated course,
  5/6 -> 3/6 on eval_graph.py. Re-test with eval_graph.py before changing.)

NAMING RULES (the SAME idea must get the SAME name across lectures):
- Use the concept's standard, widely-used name and keep it stable.
- Do NOT invent a synonym when the lecture already uses a standard term.

STRICT EXCLUSIONS:
- Only real domain concepts. EXCLUDE: agendas, "topics for today", instructor
  bio, course logistics, poll/exercise headers, named examples, section-title filler.
- 5-15 concepts per lecture. Do not dump every heading.
Return ONLY a JSON array: [{"name":..,"definition":..,"related":[..]}]."""


def canon(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().casefold()


def safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "-", name).strip().strip(".") or "unnamed"


def _client():
    load_dotenv(str(Path(__file__).parent / ".env"))
    from canvas import gemini_key
    return genai.Client(api_key=gemini_key())


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
            transient = isinstance(e, json.JSONDecodeError) or any(
                c in msg for c in ("503", "429", "500", "UNAVAILABLE", "RESOURCE_EXHAUSTED"))
            if not transient or attempt == 5:
                raise
            wait = min(60, 4 * 2 ** attempt)
            print(f"      transient ({msg[:40]}...) retry in {wait}s")
            time.sleep(wait)


def pass1(slug):
    """Extract concepts per lecture note, cached by (note + prompt + model)."""
    import hashlib
    client = _client()
    XCACHE.mkdir(parents=True, exist_ok=True)
    out = {}
    # Cache on the prompt and model too, not just the note: otherwise editing the
    # extraction prompt silently reuses old results and iteration looks like a no-op.
    recipe = hashlib.sha256((PROMPT + MODEL).encode()).hexdigest()[:12]
    notes = sorted(p for p in notes_dir(slug).glob("*.md") if is_lecture(p))
    print(f"pass 1 [{slug}]: extracting concepts from {len(notes)} lecture note(s)")
    failed = 0
    for p in notes:
        text = p.read_text()
        h = hashlib.sha256(text.encode()).hexdigest()
        cache = XCACHE / f"{recipe}-{h}.json"
        if cache.exists():
            out[p.stem] = json.loads(cache.read_text())
            continue
        try:
            concepts = extract_note(client, text)
            cache.write_text(json.dumps(concepts, indent=2))
            out[p.stem] = concepts
            print(f"  new  {p.name} ({len(concepts)} concepts)")
        except Exception as e:
            failed += 1
            print(f"       FAILED {p.name}: {str(e)[:70]} (retry next run)")
    return out, failed


def pass2(slug, per_lecture: dict, complete: bool = True):
    """Merge concepts across lectures by canonical name, write vault/<slug>/.

    `complete` says whether every lecture extracted successfully. If any failed
    (rate limit, bad JSON), this run's concept set is a SUBSET of the real one,
    so stale-purging would delete good notes — see the guard below.
    """
    vault = vault_dir(slug)
    nodes = {}
    for stem, concepts in per_lecture.items():
        for c in concepts:
            k = canon(c["name"])
            if k not in nodes:
                nodes[k] = {"name": c["name"].strip(), "definition": c.get("definition", ""),
                            "related": set(), "lectures": set()}
            nodes[k]["lectures"].add(stem)
            for r in c.get("related", []):
                nodes[k]["related"].add(canon(r))

    (vault / "concepts").mkdir(parents=True, exist_ok=True)
    (vault / "lectures").mkdir(parents=True, exist_ok=True)
    for p in notes_dir(slug).glob("*.md"):
        if is_lecture(p):
            (vault / "lectures" / p.name).write_text(p.read_text())

    # Drop concept notes from earlier runs that are no longer extracted; otherwise
    # they linger forever as orphan nodes and pollute any evaluation.
    # ONLY when this run covered every lecture: a partial run (e.g. Gemini daily
    # quota hit halfway) legitimately produces fewer concepts, and purging against
    # it deletes good notes. Leaving orphans is cosmetic; deleting data is not.
    written = {safe_filename(n["name"]) for n in nodes.values()}
    stale = [p for p in (vault / "concepts").glob("*.md") if p.stem not in written]
    if stale and complete:
        for p in stale:
            p.unlink()
        print(f"           removed {len(stale)} stale concept note(s)")
    elif stale:
        print(f"           kept {len(stale)} note(s) from previous runs "
              f"(extraction incomplete — not purging)")

    edges = 0
    for k, n in nodes.items():
        links = sorted({nodes[r]["name"] for r in n["related"] if r in nodes and r != k})
        edges += len(links)
        fm = ["---", f"lectures: [{', '.join(sorted(n['lectures']))}]", "---", ""]
        body = [f"# {n['name']}", "", n["definition"], ""]
        if links:
            body += ["## Related", *[f"- [[{l}]]" for l in links], ""]
        body += ["## Appears in", *[f"- [[{s}]]" for s in sorted(n["lectures"])], ""]
        (vault / "concepts" / f"{safe_filename(n['name'])}.md").write_text("\n".join(fm + body))

    print(f"pass 2 [{slug}]: {len(nodes)} concept nodes, {edges} links -> {vault}/")
    return nodes


def _parse_concept(path):
    t = path.read_text()
    name = re.search(r"^#\s+(.+)$", t, re.M)
    name = name.group(1).strip() if name else path.stem
    lects = re.search(r"lectures: \[(.*?)\]", t)
    lects = [x for x in (lects.group(1).split(", ") if lects else []) if x]
    defn = ""
    for line in t.split("---", 2)[-1].splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "-", "lectures")):
            defn = line; break
    links = re.findall(r"\[\[(.+?)\]\]", t.split("## Related")[1].split("## Appears in")[0]) \
        if "## Related" in t else []
    return name, {"lects": lects, "definition": defn, "links": links}


def graph_data(slug):
    files = list((vault_dir(slug) / "concepts").glob("*.md"))
    parsed = dict(_parse_concept(f) for f in files)
    deg, edges, seen = {}, [], set()
    for name, d in parsed.items():
        for l in d["links"]:
            if l in parsed and frozenset((name, l)) not in seen:
                seen.add(frozenset((name, l))); edges.append({"s": name, "t": l})
                deg[name] = deg.get(name, 0) + 1; deg[l] = deg.get(l, 0) + 1
    nodes = [{"id": n, "lect": (d["lects"] or ["?"])[0], "degree": deg.get(n, 0)}
             for n, d in parsed.items()]
    return {"nodes": nodes, "edges": edges}


def concept_data(slug, name):
    f = vault_dir(slug) / "concepts" / f"{safe_filename(name)}.md"
    if not f.exists():
        return None
    n, d = _parse_concept(f)
    return {"name": n, "definition": d["definition"], "links": d["links"], "lectures": d["lects"]}


def build(slug):
    per, failed = pass1(slug)
    if per:
        pass2(slug, per, complete=(failed == 0))


def main():
    p = argparse.ArgumentParser(description="Extract concept graph for one course")
    p.add_argument("slug", help="course slug, e.g. DS4400 (matches notes/<slug>/)")
    build(p.parse_args().slug)


if __name__ == "__main__":
    main()
