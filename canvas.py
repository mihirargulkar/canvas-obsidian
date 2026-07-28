#!/usr/bin/env python3
"""Phase 1: deterministic Canvas due-date CLI. No LLM, no DB — live API only.

Usage:
    python canvas.py list [--all]
    python canvas.py due  [--days N] [--all]
"""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from canvasapi import Canvas
from dotenv import load_dotenv

LOCAL_TZ = ZoneInfo("America/New_York")  # ponytail: hardcoded to user's zone; read from Canvas profile if it ever differs


# --- pure logic (unit-tested in test_canvas.py) -----------------------------

def term_code(term_name):
    """Leading 6-digit Northeastern term code, or None (advising/group terms)."""
    m = re.search(r"\d{6}", term_name or "")
    return m.group(0) if m else None


def parse_due(due_at):
    """Canvas ISO 'due_at' (UTC, may end in Z) -> aware datetime, or None."""
    if not due_at:
        return None
    return datetime.fromisoformat(due_at.replace("Z", "+00:00"))


def in_window(due_at, now, days):
    """True if due_at falls within [now, now+days]. now must be tz-aware UTC."""
    due = parse_due(due_at)
    if due is None:
        return False
    return now <= due <= now + timedelta(days=days)


# --- Canvas access ----------------------------------------------------------

def get_client():
    load_dotenv()
    url, token = os.getenv("CANVAS_URL"), os.getenv("CANVAS_TOKEN")
    if not url or not token:
        sys.exit("Missing CANVAS_URL / CANVAS_TOKEN in .env")
    return Canvas(url, token)


def current_courses(canvas, include_all=False):
    """Courses in the most-recent coded term (or all active, if include_all)."""
    courses = list(canvas.get_courses(enrollment_state="active", include=["term"]))
    coded = [(term_code(getattr(c, "term", {}).get("name")), c) for c in courses]
    if include_all:
        return [c for _, c in coded]
    codes = [code for code, _ in coded if code]
    if not codes:
        return courses
    latest = max(codes)
    return [c for code, c in coded if code == latest]


def course_label(c):
    return getattr(c, "name", f"course {c.id}")


# --- commands ---------------------------------------------------------------

def upcoming(days, courses=None):
    """Sorted (due_utc, course_code, name, points_possible) for assignments due within `days`."""
    now = datetime.now(timezone.utc)
    if courses is None:
        courses = current_courses(get_client())
    rows = []
    for c in courses:
        for a in c.get_assignments():
            if in_window(getattr(a, "due_at", None), now, days):
                rows.append((parse_due(a.due_at), course_label(c).split()[0],
                             a.name, getattr(a, "points_possible", None)))
    rows.sort(key=lambda r: r[0])
    return rows


def cmd_list(args):
    canvas = get_client()
    courses = current_courses(canvas, include_all=args.all)
    label = "all active" if args.all else "current-term"
    print(f"{len(courses)} {label} course(s):\n")
    for c in sorted(courses, key=lambda c: course_label(c)):
        term = getattr(c, "term", {}).get("name", "?")
        print(f"  {c.id:>7}  {course_label(c)}   [{term}]")


def cmd_due(args):
    all_courses = current_courses(get_client(), include_all=True) if args.all else None
    rows = upcoming(args.days, all_courses)
    print(f"Due in the next {args.days} day(s) — {len(rows)} item(s):\n")
    last_day = None
    for due, course, name, pts in rows:
        local = due.astimezone(LOCAL_TZ)
        day = local.strftime("%a %b %d")
        if day != last_day:
            print(day); last_day = day
        pts_s = f" ({pts:g} pts)" if pts else ""
        print(f"   {local:%I:%M %p}  {course:<12} {name}{pts_s}")
    if not rows:
        print("   (nothing due)")


def main():
    p = argparse.ArgumentParser(description="Canvas due-date CLI (Phase 1)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="list courses")
    pl.add_argument("--all", action="store_true", help="all active courses, not just current term")
    pl.set_defaults(func=cmd_list)

    pd = sub.add_parser("due", help="upcoming assignments")
    pd.add_argument("--days", type=int, default=7)
    pd.add_argument("--all", action="store_true", help="check all active courses, not just current term")
    pd.set_defaults(func=cmd_due)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
