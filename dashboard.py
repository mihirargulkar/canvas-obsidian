#!/usr/bin/env python3
"""Generate vault/Dashboard.md — upcoming deadlines + recent announcements — so
opening the Obsidian vault surfaces 'what's happening' without asking.

    python dashboard.py 253025
"""
import argparse
from datetime import datetime
from pathlib import Path

import canvas
import updates
from canvas import LOCAL_TZ


def build(course_id, days=14):
    rows = canvas.upcoming(days)
    data = updates.fetch_updates(course_id)

    md = ["# Dashboard", "", f"_Updated {datetime.now(LOCAL_TZ):%a %b %d, %I:%M %p}_", ""]
    md += [f"## Upcoming (next {days} days)", ""]
    if rows:
        for d, c, n, p in rows:
            pts = f" ({p:g} pts)" if p else ""
            md.append(f"- **{d.astimezone(LOCAL_TZ):%a %b %d, %I:%M %p}** — {n}{pts}  ({c})")
    else:
        md.append("- (nothing due)")

    md += ["", "## Recent announcements", ""]
    for a in data["announcements"][:8]:
        md.append(f"- **{a['date']}** — {a['title']}")
    md += ["", "See [[announcements]] and [[syllabus]] for full text.", ""]

    out = Path("vault") / "Dashboard.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md))
    print(f"wrote {out} — {len(rows)} upcoming, {len(data['announcements'])} announcements")


def main():
    p = argparse.ArgumentParser(description="Generate vault/Dashboard.md")
    p.add_argument("course_id", type=int)
    p.add_argument("--days", type=int, default=14)
    a = p.parse_args()
    build(a.course_id, a.days)


if __name__ == "__main__":
    main()
