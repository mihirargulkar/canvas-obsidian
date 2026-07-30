#!/usr/bin/env python3
"""Sync every class you're taking this semester, and report what's new.

    python -m canvas_vault.sync                 # all current courses
    python -m canvas_vault.sync --only DS4400   # just one class
    python -m canvas_vault.sync --list          # show detected classes, change nothing
    python -m canvas_vault.sync --quiet         # only print if something changed (for cron/launchd)

Re-runnable and cheap: ingestion and extraction are content-hash cached, the
search index updates incrementally, and unchanged files aren't re-downloaded.
Safe to run daily.
"""
import argparse
import sys
from datetime import datetime

from . import chdir_root
from . import chat
from . import changes
from . import dashboard
from .course import Course


def run_sync(only=None, limit=None, do_index=True, deep=True) -> tuple[list, str]:
    """Sync courses and return (courses, human-readable what's-new summary).

    Shared by the CLI and the MCP `refresh` tool so both behave identically.
    """
    courses = Course.current()
    if only:
        courses = [c for c in courses if c.slug.lower() == only.lower()]
        if not courses:
            raise SystemExit(f"no current course with slug {only!r}")

    per_course = {}
    for c in courses:
        before = c.changes_since_last_sync()   # diff BEFORE syncing overwrites state
        result = c.sync(limit, deep=deep)
        # New lecture files are what the pipeline actually spends money on, so
        # name them; failed steps must surface too, or a nightly run whose quota
        # is exhausted reports "no changes" forever.
        before["files"] = result.get("new_files", [])
        before["failed"] = result.get("failed", [])
        # A shallow run doesn't transcribe, but it should still notice a deck
        # that has appeared on Canvas rather than implying nothing was posted.
        before["pending"] = [] if deep else c.pending_files()
        per_course[c.slug] = before

    try:
        dashboard.overview()      # one restricted course must not sink the run
    except Exception as e:
        print(f"  ! cross-class dashboard skipped — {type(e).__name__}: {str(e)[:80]}")
    n_changed = chat.index(quiet=True) if do_index else 0
    return courses, changes.summarise(per_course, n_changed)


def main():
    chdir_root()      # data paths are relative to the repo root
    p = argparse.ArgumentParser(description="Sync all current Canvas classes")
    p.add_argument("--only", metavar="SLUG", help="sync just this course slug")
    p.add_argument("--list", action="store_true", help="list detected classes and exit")
    p.add_argument("--limit", type=int, default=None, help="first N files per course (trial run)")
    p.add_argument("--no-index", action="store_true", help="skip updating the search index")
    p.add_argument("--quiet", action="store_true",
                   help="suppress output unless something changed (for scheduled runs)")
    a = p.parse_args()

    if a.list:
        courses = Course.current()
        print(f"{len(courses)} current class(es):")
        for c in courses:
            print(f"  {c.slug:<10} {c.id:>7}  {c.name}")
        return

    if a.quiet:                     # scheduled run: stay silent on a no-op day
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            courses, summary = run_sync(a.only, a.limit, not a.no_index)
        if not summary.startswith("No changes"):
            print(f"[{datetime.now():%Y-%m-%d %H:%M}] "
                  f"{', '.join(c.slug for c in courses)}\n{summary}")
        return

    courses, summary = run_sync(a.only, a.limit, not a.no_index)
    print(f"\nsynced {len(courses)} class(es): {', '.join(c.slug for c in courses)}")
    print(summary)


if __name__ == "__main__":
    main()
