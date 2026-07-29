#!/usr/bin/env python3
"""Generate Obsidian dashboards:
  - vault/Dashboard.md          — deadlines across ALL current classes
  - vault/<slug>/Dashboard.md   — one class: its deadlines + recent announcements

    python dashboard.py           # all current classes
    python dashboard.py 253025    # just that course's per-class dashboard
"""
import argparse
from datetime import datetime
from pathlib import Path

import canvas
import updates
from canvas import local_tz


def overview(days=14):
    """Top-level vault/Dashboard.md — every class's upcoming deadlines."""
    rows = canvas.upcoming(days)
    LOCAL_TZ = local_tz()
    md = ["# Dashboard — all classes", "",
          f"_Updated {datetime.now(LOCAL_TZ):%a %b %d, %I:%M %p}_", "",
          f"## Due in the next {days} days", ""]
    if rows:
        for d, c, n, p in rows:
            pts = f" ({p:g} pts)" if p else ""
            md.append(f"- **{d.astimezone(LOCAL_TZ):%a %b %d, %I:%M %p}** — {n}{pts}  ([[{c}/Dashboard|{c}]])")
    else:
        md.append("- (nothing due)")
    Path("vault").mkdir(parents=True, exist_ok=True)
    (Path("vault") / "Dashboard.md").write_text("\n".join(md) + "\n")
    print(f"wrote vault/Dashboard.md — {len(rows)} upcoming across all classes")


def course_dashboard(course, days=14, data=None):
    """vault/<slug>/Dashboard.md — one class's deadlines + recent announcements.
    `course` is a course.Course (has .slug/.id)."""
    slug = course.slug
    rows = course.upcoming(days)          # scoped: one course's assignments only
    data = data or updates.fetch_updates(course.id)
    LOCAL_TZ = local_tz()
    md = [f"# {slug} — Dashboard", "", f"_Updated {datetime.now(LOCAL_TZ):%a %b %d, %I:%M %p}_", "",
          f"## Due in the next {days} days", ""]
    md += [f"- **{d.astimezone(LOCAL_TZ):%a %b %d, %I:%M %p}** — {n}" + (f" ({p:g} pts)" if p else "")
           for d, c, n, p in rows] or ["- (nothing due)"]
    md += ["", "## Recent announcements", ""]
    md += [f"- **{a['date']}** — {a['title']}" for a in data["announcements"][:8]]
    md += ["", "See [[updates/announcements]] and [[updates/syllabus]] for full text.", ""]
    out = Path("vault") / slug / "Dashboard.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md))
    print(f"wrote {out}")


def main():
    p = argparse.ArgumentParser(description="Generate Obsidian dashboards")
    p.add_argument("course_id", type=int, nargs="?", help="one course; omit for all-class overview")
    p.add_argument("--days", type=int, default=14)
    a = p.parse_args()
    from course import Course
    if a.course_id:
        course_dashboard(Course.get(a.course_id), a.days)
    else:
        overview(a.days)
        for c in Course.current():
            course_dashboard(c, a.days)


if __name__ == "__main__":
    main()
