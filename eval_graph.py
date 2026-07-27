#!/usr/bin/env python3
"""Phase 3 eval gate: score vault/ against a hand-labeled gold set.

Gold set = my reading of DS4400 Lecture 4 (Linear Regression) + one cross-lecture
pair. Converts "meaningful links" from a vibe into pass/fail.

    python eval_graph.py
"""
import re
from pathlib import Path

VAULT = Path("vault/concepts")

# pairs that SHOULD be directly linked (concept map edges a student would draw)
GOOD_PAIRS = [
    ("Linear Regression", "Mean Squared Error"),
    ("Mean Squared Error", "Gradient Descent"),
    ("Gradient Descent", "Learning Rate"),
    ("Linear Regression", "Regularization"),
    ("Linear Regression", "Basis Functions"),
    ("Gradient Descent", "Derivative"),   # cross-lecture: L4 <-> L1
]
# generic/administrative items that must NOT exist as concept nodes
BAD_CONCEPTS = ["Today's Agenda", "Instructor Background", "Practice Exercises",
                "Topics for Today", "Course Overview", "Interactive Polls"]


def canon(s): return re.sub(r"\s+", " ", s).strip().casefold()


def load_graph():
    """Return (node_canon->display, adjacency set of frozenset{a,b})."""
    nodes, adj = {}, set()
    for f in VAULT.glob("*.md"):
        txt = f.read_text()
        name = re.search(r"^#\s+(.+)$", txt, re.M)
        name = name.group(1).strip() if name else f.stem
        nc = canon(name)
        nodes[nc] = name
        rel = txt.split("## Related", 1)
        if len(rel) == 2:
            body = rel[1].split("## Appears in")[0]
            for m in re.findall(r"\[\[(.+?)\]\]", body):
                adj.add(frozenset((nc, canon(m))))
    return nodes, adj


def match(name, nodes):
    """Resolve a gold name to a node canon: exact, else shortest containing match."""
    c = canon(name)
    if c in nodes:
        return c
    cands = [n for n in nodes if c in n or n in c]
    return min(cands, key=len) if cands else None


def distance(a, b, adj):
    """Shortest hop count between two node canons (BFS), or None if unreachable.
    A concept map link is 'meaningful' at <=2 hops through a named intermediate
    concept (e.g. Gradient Descent -> Gradient -> Derivative)."""
    if a == b:
        return 0
    nbr = {}
    for e in adj:
        x, y = tuple(e)
        nbr.setdefault(x, set()).add(y)
        nbr.setdefault(y, set()).add(x)
    seen, frontier, d = {a}, {a}, 0
    while frontier and d < 4:
        d += 1
        nxt = set().union(*(nbr.get(n, set()) for n in frontier)) - seen
        if b in nxt:
            return d
        seen |= nxt
        frontier = nxt
    return None


def main():
    if not VAULT.exists():
        print("no vault yet — run extract.py first"); return
    nodes, adj = load_graph()
    print(f"vault: {len(nodes)} concept nodes, {len(adj)} unique edges\n")

    print("GOOD PAIRS (want a meaningful connection, <=2 hops):")
    good_ok = 0
    for a, b in GOOD_PAIRS:
        ma, mb = match(a, nodes), match(b, nodes)
        if not ma or not mb:
            miss = a if not ma else b
            print(f"  FAIL  {a} — {b}   (no node for '{miss}')")
            continue
        d = distance(ma, mb, adj)
        if d and d <= 2:
            good_ok += 1
            hop = "direct" if d == 1 else f"{d} hops"
            print(f"  PASS  {a} — {b}   ({hop})")
        elif d:
            print(f"  WEAK  {a} — {b}   ({d} hops apart)")
        else:
            print(f"  FAIL  {a} — {b}   (unreachable)")

    print("\nBAD CONCEPTS (want ABSENT as nodes):")
    bad_ok = 0
    for b in BAD_CONCEPTS:
        m = match(b, nodes)
        if m is None:
            bad_ok += 1
            print(f"  PASS  '{b}' absent")
        else:
            print(f"  FAIL  '{b}' present as node '{nodes[m]}'")

    print(f"\nSCORE: good links {good_ok}/{len(GOOD_PAIRS)} | "
          f"generic excluded {bad_ok}/{len(BAD_CONCEPTS)}")


if __name__ == "__main__":
    main()
