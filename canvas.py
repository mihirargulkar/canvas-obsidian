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

DEFAULT_TZ = "America/New_York"   # only used if Canvas and the OS both stay silent


def ttl_cache(seconds=300):
    """Memoize a function for `seconds`. Canvas data changes on the order of
    hours, but a single sync/conversation hits these endpoints repeatedly.
    ponytail: process-local dict; swap for real caching only if this ever runs
    as a long-lived multi-user service."""
    import functools
    import time as _t

    def deco(fn):
        store = {}

        @functools.wraps(fn)
        def wrap(*args):
            hit = store.get(args)
            if hit and _t.monotonic() - hit[0] < seconds:
                return hit[1]
            val = fn(*args)
            store[args] = (_t.monotonic(), val)
            return val

        wrap.cache_clear = store.clear
        return wrap

    return deco


# --- pure logic (unit-tested in test_canvas.py) -----------------------------

def term_code(term_name):
    """Longest digit-run in a term name, used to group concurrent terms.

    Institutions encode terms differently ('202650_2B Summer 2026', 'Fall 2026',
    '2026FA'). Taking the longest digit run groups sessions of the same term
    together (202650_2A and _2B -> '202650'; 'Spring 2026'/'Fall 2026' -> '2026'),
    which is what we want: a student can be enrolled in two concurrent sessions.
    Returns None for undated terms ('Default Term'), which fall back to term id.
    """
    runs = re.findall(r"\d+", term_name or "")
    return max(runs, key=len) if runs else None


def _term_ended(term, now):
    """True if this term has a known end date that has passed."""
    end = (term or {}).get("end_at")
    if not end:
        return False
    try:
        return datetime.fromisoformat(end.replace("Z", "+00:00")) < now
    except ValueError:
        return False


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
        sys.exit("Missing CANVAS_URL / CANVAS_TOKEN in .env — copy .env.example to "
                 ".env and fill it in (Canvas -> Account -> Settings -> New Access Token).")
    return Canvas(url, token)


def gemini_key():
    """Fail with instructions rather than a library traceback when unset."""
    load_dotenv()
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        sys.exit("Missing GEMINI_API_KEY in .env — get a free key at "
                 "https://aistudio.google.com/app/apikey (used to transcribe slides).")
    return key


def current_courses(canvas, include_all=False):
    """The classes you're taking now (or every active course, if include_all).

    `enrollment_state=active` is not enough on its own: many institutions never
    set term end dates, so Canvas keeps returning years of old courses. Layered:
      1. drop terms with a known end date in the past (correct where dates exist);
      2. group the rest by term code and keep the newest group (handles schools
         that run concurrent sessions, e.g. Summer A + Summer B);
      3. if no term names carry digits, fall back to the highest term id
         (Canvas term ids increase over time).
    """
    courses = list(canvas.get_courses(enrollment_state="active", include=["term"]))
    if include_all:
        return courses

    now = datetime.now(timezone.utc)
    live = [c for c in courses if not _term_ended(getattr(c, "term", {}), now)] or courses

    coded = [(term_code((getattr(c, "term", {}) or {}).get("name")), c) for c in live]
    codes = [code for code, _ in coded if code]
    if codes:
        latest = max(codes, key=lambda s: (len(s), s))   # numeric-ish, longest wins ties
        return [c for code, c in coded if code == latest]

    ids = [(getattr(c, "term", {}) or {}).get("id") or 0 for c in live]
    if any(ids):
        newest = max(ids)
        return [c for c in live if ((getattr(c, "term", {}) or {}).get("id") or 0) == newest]
    return live


@ttl_cache(3600)
def local_tz():
    """The timezone to render due dates in.

    CANVAS_TZ env override -> the Canvas account's own timezone (what the LMS
    shows you) -> this machine's timezone -> DEFAULT_TZ. Deadlines displayed in
    the wrong zone are worse than useless, so this is worth getting right.
    """
    name = os.getenv("CANVAS_TZ")
    if name:
        return ZoneInfo(name)
    try:
        tz = get_client().get_current_user().get_profile().get("time_zone")
        if tz:
            return ZoneInfo(tz)
    except Exception:
        pass
    return datetime.now().astimezone().tzinfo or ZoneInfo(DEFAULT_TZ)


@ttl_cache(300)
def get_course(course_id):
    """Fetch (and briefly cache) one canvasapi Course. Shared across Course
    instances so repeated MCP tool calls don't re-fetch the same course."""
    return get_client().get_course(course_id, include=["syllabus_body"])


def course_label(c):
    return getattr(c, "name", f"course {c.id}")


def slug_of(c):
    """Short course key, e.g. 'DS4400' — first token of the course name."""
    return course_label(c).split()[0]


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
        local = due.astimezone(local_tz())
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
