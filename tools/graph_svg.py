#!/usr/bin/env python3
"""Render a course's concept graph to a standalone SVG (for a README, a poster,
or just to see the shape of a course).

    python tools/graph_svg.py DS4400
    python tools/graph_svg.py DS4400 --out docs/ml.svg --size 2000x1100

Reads vault/<SLUG>/concepts/ — run `extract.py <SLUG>` first. Layout is a seeded
spring embedder, so the same graph always renders identically (no diff churn).
No JavaScript and no extra dependencies: the output is a plain static SVG.
"""
import argparse
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import extract  # noqa: E402

# colour-blind-friendly categorical ramp (Tableau 10 + extensions), one per lecture
PALETTE = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2",
           "#EECA3B", "#B279A2", "#FF9DA6", "#9D755D", "#8CD17D",
           "#B6992D", "#86BCB6", "#D37295", "#A0CBE8", "#FFBE7D", "#79706E"]


def layout(nodes, edges, iterations=600, seed=7):
    """Seeded Fruchterman-Reingold. Returns {node: [x, y]} in arbitrary units.

    ponytail: O(n^2) repulsion — fine to a few hundred concepts (a semester of
    one course). Switch to Barnes-Hut if a vault ever gets into the thousands.
    """
    random.seed(seed)
    pos = {n: [random.uniform(-1, 1), random.uniform(-1, 1)] for n in nodes}
    k = 0.55 / math.sqrt(max(len(nodes), 1))
    names = list(nodes)
    for step in range(iterations):
        t = 0.10 * (1 - step / iterations) + 0.002
        disp = {n: [0.0, 0.0] for n in nodes}
        for i, a in enumerate(names):                       # repulsion
            for b in names[i + 1:]:
                dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
                f = (k * k) / (dx * dx + dy * dy + 1e-6)
                disp[a][0] += dx * f; disp[a][1] += dy * f
                disp[b][0] -= dx * f; disp[b][1] -= dy * f
        for a, b in edges:                                  # attraction
            dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
            d = math.hypot(dx, dy) + 1e-6
            f = (d * d) / k * 0.02
            disp[a][0] -= dx / d * f; disp[a][1] -= dy / d * f
            disp[b][0] += dx / d * f; disp[b][1] += dy / d * f
        for n in nodes:                                     # gravity + capped step
            disp[n][0] -= pos[n][0] * 0.35
            disp[n][1] -= pos[n][1] * 0.35
            dl = math.hypot(*disp[n]) + 1e-9
            pos[n][0] += disp[n][0] / dl * min(dl, t)
            pos[n][1] += disp[n][1] / dl * min(dl, t)
    return pos


def render(slug, out, width, height, label_degree, title=None):
    g = extract.graph_data(slug)
    nodes = {n["id"]: n for n in g["nodes"]}
    if not nodes:
        sys.exit(f"no concepts in vault/{slug}/concepts — run: python extract.py {slug}")
    edges = [(e["s"], e["t"]) for e in g["edges"] if e["s"] in nodes and e["t"] in nodes]

    color = {l: PALETTE[i % len(PALETTE)]
             for i, l in enumerate(sorted({n["lect"] for n in nodes.values()}))}
    pos = layout(nodes, edges)

    xs = [p[0] for p in pos.values()]; ys = [p[1] for p in pos.values()]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    pad = 90
    def sx(x): return pad + (x - minx) / ((maxx - minx) or 1) * (width - 2 * pad)
    def sy(y): return pad + (y - miny) / ((maxy - miny) or 1) * (height - 2 * pad)
    def radius(n): return 4 + math.sqrt(nodes[n]["degree"]) * 2.6
    def esc(s): return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
           f'width="{width}" height="{height}" '
           f'font-family="Inter,system-ui,-apple-system,sans-serif">',
           f'<rect width="{width}" height="{height}" fill="#17161a"/>',
           '<defs><filter id="glow" x="-70%" y="-70%" width="240%" height="240%">'
           '<feGaussianBlur stdDeviation="4" result="b"/>'
           '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
           '</filter></defs>']

    for a, b in edges:
        svg.append(f'<line x1="{sx(pos[a][0]):.1f}" y1="{sy(pos[a][1]):.1f}" '
                   f'x2="{sx(pos[b][0]):.1f}" y2="{sy(pos[b][1]):.1f}" '
                   f'stroke="#4a4954" stroke-width="1" stroke-opacity="0.55"/>')
    for n, d in sorted(nodes.items(), key=lambda kv: kv[1]["degree"]):   # hubs on top
        svg.append(f'<circle cx="{sx(pos[n][0]):.1f}" cy="{sy(pos[n][1]):.1f}" '
                   f'r="{radius(n):.1f}" fill="{color[d["lect"]]}" filter="url(#glow)" '
                   f'fill-opacity="0.95"/>')
    for n, d in nodes.items():
        if d["degree"] >= label_degree:
            svg.append(f'<text x="{sx(pos[n][0]) + radius(n) + 5:.1f}" '
                       f'y="{sy(pos[n][1]) + 4:.1f}" '
                       f'font-size="{15 if d["degree"] >= 9 else 12.5}" fill="#eae7e0">'
                       f'{esc(n)}</text>')

    svg.append(f'<text x="{pad}" y="46" font-size="24" font-weight="600" fill="#ffffff">'
               f'{esc(title or f"{slug} — concept graph")}</text>')
    svg.append(f'<text x="{pad}" y="72" font-size="14.5" fill="#9a978f">'
               f'{len(nodes)} concepts · {len(edges)} links · '
               f'auto-extracted from {len(color)} lectures · colour = source lecture</text>')
    svg.append("</svg>")

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(svg))
    print(f"wrote {out} — {len(nodes)} concepts, {len(edges)} links, {len(color)} lectures")


def main():
    p = argparse.ArgumentParser(description="Render a course concept graph to SVG")
    p.add_argument("course", help="course slug, e.g. DS4400 (matches vault/<slug>/)")
    p.add_argument("--out", default=None, help="output path (default docs/<slug>-graph.svg)")
    p.add_argument("--size", default="1600x900", help="WIDTHxHEIGHT (default 1600x900)")
    p.add_argument("--label-degree", type=int, default=5,
                   help="only label concepts with at least this many links (default 5)")
    p.add_argument("--title", default=None, help='heading text (default "<SLUG> — concept graph")')
    a = p.parse_args()
    w, _, h = a.size.partition("x")
    render(a.course, a.out or f"docs/{a.course}-graph.svg",
           int(w), int(h), a.label_degree, a.title)


if __name__ == "__main__":
    main()
