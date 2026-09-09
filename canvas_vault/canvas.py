#!/usr/bin/env python3
"""Phase 1: deterministic Canvas due-date CLI. No LLM, no DB — live API only.

Usage:
    python -m canvas_vault.canvas list [--all]
    python -m canvas_vault.canvas due  [--days N] [--all]
    python -m canvas_vault.canvas grades [--summary]
"""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from canvasapi import Canvas
from dotenv import load_dotenv

from . import chdir_root

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


class CanvasError(RuntimeError):
    """A Canvas call failed for a reason the user can actually fix.

    get_client() already handles *missing* credentials. Wrong ones were the gap:
    a typo'd CANVAS_URL surfaced as a raw canvasapi traceback ending in
    "ResourceDoesNotExist: Not Found", which says nothing about what to change.
    Found by running setup.sh on a clean clone the way a new user would.
    """


def _explain(exc) -> str:
    name, text = type(exc).__name__, str(exc)
    url = os.getenv("CANVAS_URL") or "(unset)"
    if "DoesNotExist" in name or "404" in text:
        return (f"Canvas returned 'not found' for {url}\n"
                f"CANVAS_URL is probably wrong. It should be just your school's "
                f"Canvas host, e.g. https://yourschool.instructure.com — no path, "
                f"no /courses. Fix it in .env and try again.")
    if "Unauthorized" in name or "InvalidAccessToken" in name or "401" in text:
        return ("Canvas rejected your access token.\n"
                "Generate a new one at Canvas > Account > Settings > New Access "
                "Token, then update CANVAS_TOKEN in .env. Tokens can expire.")
    if "Forbidden" in name or "403" in text:
        return ("Canvas refused the request (403). The token may lack permission, "
                "or your institution restricts API access.")
    return (f"Canvas request to {url} failed: {name}: {text[:160]}\n"
            f"Check CANVAS_URL and CANVAS_TOKEN in .env.")


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
    try:
        courses = list(canvas.get_courses(enrollment_state="active", include=["term"]))
    except Exception as e:
        # Every path that reaches Canvas comes through here, so one guard covers
        # the CLIs, the sync and the MCP tools. Raised, not sys.exit: this is
        # library code and the MCP server must survive a bad call.
        raise CanvasError(_explain(e)) from e
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
    # Only ask Canvas if we could actually authenticate. get_client() calls
    # sys.exit when credentials are missing, and SystemExit is not an Exception,
    # so it used to sail straight through the handler below and kill the process:
    # formatting a date without a .env took down the whole run.
    load_dotenv()
    if os.getenv("CANVAS_URL") and os.getenv("CANVAS_TOKEN"):
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
    # Canvas can send name: null, so `or` rather than a getattr default
    return getattr(c, "name", None) or f"course {c.id}"


def slug_from(code, name, course_id):
    """Filesystem-safe short course key, e.g. 'DS4400'.

    THE single definition of a slug: Course.slug and slug_of both delegate here.
    They disagreed once, and the two halves of the codebase then wrote data to
    vault/DS4400/ while linking to vault/DS/.

    Prefers Canvas's course_code ("DS4400.50397.202650" -> "DS4400"). Falls back
    to the name, joining a leading letters+digits pair so "DS 4400 Machine
    Learning" gives "DS4400" and not "DS" (which would collide with every other
    DS course). Sanitised, because a name may contain "/" (cross-listed courses)
    or ".." and the result is used as a path component.
    """
    raw = ((code or "").split(".")[0]).strip()
    if not raw:
        tokens = (name or "").split()
        if tokens:
            raw = (tokens[0] + tokens[1]
                   if len(tokens) > 1 and tokens[0].isalpha() and tokens[1][:1].isdigit()
                   else tokens[0])
    return re.sub(r"[^A-Za-z0-9_-]", "", raw) or f"course-{course_id}"


def slug_of(c):
    """Short course key for a live canvasapi course object.

    Passes the raw name, not course_label(): the label synthesises "course 5" for
    a null name, which the parser would turn into "course5" while Course.slug
    produces "course-5" for the same course.
    """
    return slug_from(getattr(c, "course_code", "") or "",
                     getattr(c, "name", None) or "", c.id)


# --- commands ---------------------------------------------------------------

def overdue(days_back=14, courses=None):
    """Assignments whose due date has passed within the last `days_back` days.

    upcoming() is forward-only, so "what am I overdue on?" needs its mirror."""
    now = datetime.now(timezone.utc)
    rows = [r for r in _rows(courses)
            if r[0] and now - timedelta(days=days_back) <= r[0] < now]
    rows.sort(key=lambda r: r[0], reverse=True)
    return rows


def _rows(courses=None):
    """(due_utc, slug, name, points) for every dated assignment, unfiltered."""
    if courses is None:
        courses = current_courses(get_client())
    out = []
    for c in courses:
        try:
            assignments = list(c.get_assignments())
        except Exception as e:
            print(f"  ! {slug_of(c)}: assignments unavailable ({type(e).__name__})",
                  file=sys.stderr)
            continue
        for a in assignments:
            due = parse_due(getattr(a, "due_at", None))
            if due:
                out.append((due, slug_of(c), a.name, getattr(a, "points_possible", None)))
    return out


def upcoming(days, courses=None):
    """Sorted (due_utc, course_code, name, points_possible) for assignments due within `days`."""
    now = datetime.now(timezone.utc)
    if courses is None:
        courses = current_courses(get_client())
    rows = []
    for c in courses:
        try:
            assignments = list(c.get_assignments())
        except Exception as e:
            # One course with a restricted Assignments tab must not take down
            # deadlines for every other class.
            print(f"  ! {slug_of(c)}: assignments unavailable ({type(e).__name__})",
                  file=sys.stderr)
            continue
        for a in assignments:
            if in_window(getattr(a, "due_at", None), now, days):
                rows.append((parse_due(a.due_at), slug_of(c),
                             a.name, getattr(a, "points_possible", None)))
    rows.sort(key=lambda r: r[0])
    return rows


def is_locked(f) -> bool:
    """True if Canvas lists this file but will not serve it to this student.

    Instructors gate lectures behind module dates, so Canvas returns full
    metadata with `locked_for_user` set and an EMPTY url. Calling download() on
    that raises ResourceDoesNotExist, which reads like a missing file rather
    than an unreleased one.
    """
    return bool(getattr(f, "locked_for_user", False)) or not getattr(f, "url", "")


def course_files(c):
    """Every file in a course, however the instructor chose to expose it.

    get_files() 403s whenever the Files tab is restricted, which is common and
    does NOT mean the files are unreachable: anything published in a Module is
    still fetchable by id. A real course returned zero files this way while
    holding its lectures, homework, syllabus and schedule in modules, and
    pending_files() reported [] — "nothing pending" rather than "could not
    look", which is the failure shape this codebase keeps having to fix.

    Deduplicated by file id, so a file listed in both places appears once.
    """
    out, seen = [], set()
    try:
        for f in c.get_files():
            if f.id not in seen:
                seen.add(f.id)
                out.append(f)
    except Exception as e:
        print(f"  ! {slug_of(c)}: Files tab unavailable ({type(e).__name__}), "
              f"reading modules instead", file=sys.stderr)
    try:
        for m in c.get_modules():
            for i in m.get_module_items():
                fid = getattr(i, "content_id", None)
                if getattr(i, "type", "") != "File" or fid is None or fid in seen:
                    continue
                seen.add(fid)
                try:
                    out.append(c.get_file(fid))
                except Exception as e:
                    print(f"  ! {slug_of(c)}: module file {i.title!r} unreadable "
                          f"({type(e).__name__})", file=sys.stderr)
    except Exception as e:
        print(f"  ! {slug_of(c)}: modules unavailable ({type(e).__name__})",
              file=sys.stderr)
    return out


MAX_GRADE_ITEMS = 60      # a term's graded work; keeps the MCP payload small


def grades(courses=None, items=True):
    """Current grade per course, and optionally the individual graded items.

    Canvas reports two totals and they answer different questions:
      current_score — counts only what has been graded so far
      final_score   — treats everything ungraded as a zero
    Early in a term these diverge wildly, so reporting one alone is misleading.
    Both are passed through unchanged; nothing here recomputes a grade, because
    weighting rules live in the syllabus and guessing them would invent numbers.

    A course whose grades the instructor hides returns an `error` string rather
    than raising, so one restricted class cannot hide every other class's marks.
    """
    if courses is None:
        courses = current_courses(get_client())
    out = []
    for c in courses:
        row = {"course": slug_of(c), "name": course_label(c)}
        try:
            enr = next((e for e in c.get_enrollments(user_id="self")
                        if getattr(e, "type", "") in ("StudentEnrollment", "")), None)
            g = (getattr(enr, "grades", None) or {}) if enr else {}
            row.update(current_score=g.get("current_score"),
                       final_score=g.get("final_score"),
                       letter=g.get("current_grade") or g.get("final_grade"))
        except Exception as e:
            row["error"] = f"grades unavailable ({type(e).__name__})"
            print(f"  ! {row['course']}: {row['error']}", file=sys.stderr)
        if items:
            row["items"] = _graded_items(c)
        out.append(row)
    return out


def _graded_items(c):
    """Per-assignment scores for this course, most recently graded first.

    Ungraded-but-submitted work is included with score None: "what have I not
    got back yet" is as much a grade question as "what did I score".
    """
    try:
        subs = list(c.get_multiple_submissions(student_ids=["self"],
                                               include=["assignment"]))
    except Exception as e:
        print(f"  ! {slug_of(c)}: submissions unavailable ({type(e).__name__})",
              file=sys.stderr)
        return []
    rows = []
    for s in subs:
        a = getattr(s, "assignment", None) or {}
        score, state = getattr(s, "score", None), getattr(s, "workflow_state", None)
        if score is None and state in (None, "unsubmitted"):
            continue                      # never attempted and never marked
        # Pass/fail work carries its result in `grade` ("complete"), not `score`,
        # so scoring alone renders a marked assignment as if it were unmarked.
        grade = getattr(s, "grade", None)
        if getattr(s, "excused", False):
            status = "excused"
        elif score is not None or grade:
            status = "graded"
        elif state == "graded":
            # Canvas really does return workflow_state=graded with neither score
            # nor grade set. Calling that "graded" implies a mark that isn't
            # there, and a student reading it would think they had been marked.
            status = "no mark recorded"
        else:
            status = state or "pending"
        rows.append({"name": a.get("name") or f"assignment {getattr(s, 'assignment_id', '?')}",
                     "score": score,
                     "grade": grade,
                     "out_of": a.get("points_possible"),
                     "graded_at": getattr(s, "graded_at", None),
                     "status": status})
    rows.sort(key=lambda r: (r["graded_at"] or ""), reverse=True)
    return rows[:MAX_GRADE_ITEMS]


def cmd_grades(args):
    rows = grades(items=not args.summary)
    for r in rows:
        if r.get("error"):
            print(f"{r['course']:<12} {r['error']}")
            continue
        cur, fin = r.get("current_score"), r.get("final_score")
        cur_s = f"{cur:g}%" if cur is not None else "n/a"
        fin_s = f"{fin:g}%" if fin is not None else "n/a"
        letter = f"  {r['letter']}" if r.get("letter") else ""
        print(f"{r['course']:<12} current {cur_s:>6}   if-unsubmitted-zero {fin_s:>6}{letter}")
        for it in r.get("items", [])[:12]:
            if it["score"] is not None:
                got = (f"{it['score']:g}/{it['out_of']:g}" if it["out_of"]
                       else f"{it['score']:g}")
            else:
                got = it["grade"] or it["status"]
            print(f"    {got:>12}  {it['name'][:52]}")
    if not rows:
        print("(no current courses)")


def cmd_list(args):
    canvas = get_client()
    courses = current_courses(canvas, include_all=args.all)
    label = "all active" if args.all else "current-term"
    print(f"{len(courses)} {label} course(s):\n")
    for c in sorted(courses, key=lambda c: course_label(c)):
        term = (getattr(c, "term", {}) or {}).get("name", "?")
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
    chdir_root()      # data paths are relative to the repo root
    p = argparse.ArgumentParser(description="Canvas due-date CLI (Phase 1)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="list courses")
    pl.add_argument("--all", action="store_true", help="all active courses, not just current term")
    pl.set_defaults(func=cmd_list)

    pd = sub.add_parser("due", help="upcoming assignments")
    pd.add_argument("--days", type=int, default=7)
    pd.add_argument("--all", action="store_true", help="check all active courses, not just current term")
    pd.set_defaults(func=cmd_due)

    pg = sub.add_parser("grades", help="current grade per class")
    pg.add_argument("--summary", action="store_true", help="totals only, no per-assignment rows")
    pg.set_defaults(func=cmd_grades)

    args = p.parse_args()
    try:
        args.func(args)
    except CanvasError as e:
        sys.exit(f"\n{e}")


if __name__ == "__main__":
    main()
