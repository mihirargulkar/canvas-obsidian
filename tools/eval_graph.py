#!/usr/bin/env python3
"""DEVELOPMENT TOOL — scores a course's concept graph, turning "are these links
meaningful?" into a number with a control.

    python eval_graph.py [--course SLUG] [-v]

Uses tools/graph_pairs.json if present (generate it with make_graph_eval.py),
otherwise the 6 hand written pairs below. The hand set caught one real
regression and could not tell improvement from run-to-run variance, because
extraction is nondeterministic and six pairs is not a sample.

READ THE LIFT, NOT THE PERCENTAGE. "Within 2 hops" is trivially gamed by adding
edges: connect everything and score 100%. So every run also scores 400 random
concept pairs. The gold score only means something as the margin over that
baseline. Currently 61% against a random 8%.

The pairs are generated from the lecture notes and the generator never sees the
graph, so a pair may name a concept that was never extracted. That is the point:
missing nodes are the dominant failure mode and an edge-only metric hides them.

NOTE: gold pairs are per-course. Regenerate for your own classes.
"""
import argparse
import json
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

GOLD_COURSE = "DS4400"     # the course GOOD_PAIRS/BAD_CONCEPTS were written for

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


def load_graph(vault):
    """Return (node_canon->display, adjacency set of frozenset{a,b})."""
    nodes, adj = {}, set()
    for f in vault.glob("*.md"):
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


def random_baseline(nodes, adj, trials=400, seed=3):
    """Fraction of RANDOM concept pairs that sit within 2 hops.

    Without this the headline number is uninterpretable. "<=2 hops" is easy to
    win by adding edges: a dense enough graph connects everything to everything
    and scores 100% while meaning nothing. Subsumption linking in pass 2 raised
    edge count by a quarter, which is exactly the kind of change that could buy
    a better score without encoding any more real structure.

    A gold pair score is only evidence if it clears this by a wide margin.
    """
    import random as _r
    keys = list(nodes)
    if len(keys) < 4:
        return 0.0
    rng = _r.Random(seed)
    near = 0
    for _ in range(trials):
        a, b = rng.sample(keys, 2)
        d = distance(a, b, adj)
        near += bool(d and d <= 2)
    return near / trials


def load_pairs(path):
    """Generated pairs if present, else the hand written ones."""
    if path and Path(path).exists():
        rows = json.loads(Path(path).read_text())
        return [(r["a"], r["b"]) for r in rows], f"{path} (generated, n={len(rows)})"
    return GOOD_PAIRS, f"built-in hand written (n={len(GOOD_PAIRS)})"


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--course", default=GOLD_COURSE,
                   help=f"course slug to score (default {GOLD_COURSE})")
    p.add_argument("--pairs", default="tools/graph_pairs.json",
                   help="generated pair set to use if present")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="print every pair, not just the summary")
    args = p.parse_args()

    vault = Path("vault") / args.course / "concepts"
    if not vault.exists():
        print(f"no concept graph at {vault} — run: python -m canvas_vault.extract {args.course}")
        return
    if args.course != GOLD_COURSE:
        print(f"WARNING: the gold set is hand-written for {GOLD_COURSE}; results for "
              f"{args.course} are meaningless until you replace GOOD_PAIRS/BAD_CONCEPTS.\n")

    nodes, adj = load_graph(vault)
    print(f"{args.course}: {len(nodes)} concept nodes, {len(adj)} unique edges\n")

    pairs, label = load_pairs(args.pairs)
    print(f"GOOD PAIRS (want a meaningful connection, <=2 hops)  [{label}]:")
    good_ok, direct, missing, tally = 0, 0, 0, []
    for a, b in pairs:
        ma, mb = match(a, nodes), match(b, nodes)
        if not ma or not mb:
            missing += 1
            tally.append(("FAIL", a, b, f"no node for '{a if not ma else b}'"))
            continue
        d = distance(ma, mb, adj)
        if d and d <= 2:
            good_ok += 1
            direct += d == 1
            tally.append(("PASS", a, b, "direct" if d == 1 else f"{d} hops"))
        else:
            tally.append(("WEAK" if d else "FAIL", a, b,
                          f"{d} hops apart" if d else "unreachable"))
    for verdict, a, b, note in (tally if args.verbose or len(tally) <= 12 else []):
        print(f"  {verdict}  {a} — {b}   ({note})")
    if not args.verbose and len(tally) > 12:
        shown = [t for t in tally if t[0] != "PASS"][:10]
        print(f"  {good_ok}/{len(pairs)} within 2 hops ({direct} direct). "
              f"{missing} pair(s) name a concept with no node. Failures:")
        for verdict, a, b, note in shown:
            print(f"    {verdict}  {a} — {b}   ({note})")
        print("  (-v for the full list)")

    print("\nBAD CONCEPTS (want ABSENT as nodes):")
    bad_ok = 0
    for b in BAD_CONCEPTS:
        m = match(b, nodes)
        if m is None:
            bad_ok += 1
            print(f"  PASS  '{b}' absent")
        else:
            print(f"  FAIL  '{b}' present as node '{nodes[m]}'")

    base = random_baseline(nodes, adj)
    hit = good_ok / len(pairs) if pairs else 0.0
    print(f"\nSCORE: good links {good_ok}/{len(pairs)} ({hit:.0%}) | "
          f"generic excluded {bad_ok}/{len(BAD_CONCEPTS)}")
    print(f"       random-pair baseline {base:.0%} within 2 hops  ->  "
          f"lift {hit - base:+.0%}")
    if base > 0.5:
        print("       WARNING: over half of all random pairs are within 2 hops. "
              "The graph is dense enough that this metric is close to meaningless; "
              "tighten the edges or score on direct links only.")


if __name__ == "__main__":
    main()
