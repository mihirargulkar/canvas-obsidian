#!/usr/bin/env python3
"""Sync every class you're taking this semester.

    python sync.py                 # all current courses: ingest -> updates -> extract -> dashboards
    python sync.py --only DS4400   # just one class
    python sync.py --list          # show detected classes, change nothing

Re-runnable: ingestion and extraction are content-hash cached, so a second run
costs no model calls. Rebuilds the search index at the end.
"""
import argparse

import chat
import dashboard
from course import Course


def main():
    p = argparse.ArgumentParser(description="Sync all current Canvas classes")
    p.add_argument("--only", metavar="SLUG", help="sync just this course slug")
    p.add_argument("--list", action="store_true", help="list detected classes and exit")
    p.add_argument("--limit", type=int, default=None, help="first N files per course (trial run)")
    p.add_argument("--no-index", action="store_true", help="skip rebuilding the search index")
    a = p.parse_args()

    courses = Course.current()
    if a.only:
        courses = [c for c in courses if c.slug.lower() == a.only.lower()]
        if not courses:
            raise SystemExit(f"no current course with slug {a.only!r}")

    if a.list:
        print(f"{len(courses)} current class(es):")
        for c in courses:
            print(f"  {c.slug:<10} {c.id:>7}  {c.name}")
        return

    for c in courses:
        c.sync(a.limit)

    dashboard.overview()                      # cross-class deadline dashboard
    if not a.no_index:
        chat.cmd_index(None)                  # one index, tagged by course

    print(f"\nsynced {len(courses)} class(es): {', '.join(c.slug for c in courses)}")


if __name__ == "__main__":
    main()
