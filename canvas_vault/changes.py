#!/usr/bin/env python3
"""Track what's new between syncs.

Canvas is a moving target — announcements, assignments and lecture slides land
throughout the term. A sync that prints nothing tells you nothing, so we keep a
small seen-state file and diff against it.

State lives in cache/seen.json (gitignored, disposable — deleting it just means
the next sync reports everything as new).
"""
import json
from pathlib import Path

STATE = Path("cache/seen.json")


def _load() -> dict:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}          # corrupt state is not worth crashing a sync over


def _save(state: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=0, sort_keys=True))


def diff_course(slug, announcements, assignments, first_run_silent=True):
    """Compare this course against last sync. Returns
    {"announcements": [...], "assignments": [...], "first_run": bool}.

    On the very first sync everything is 'new', which is noise rather than news —
    so it's reported as first_run and the caller can summarise instead of listing.
    """
    state = _load()
    prev = state.get(slug, {})
    first_run = not prev

    seen_ann = set(prev.get("announcements", []))
    seen_asg = set(prev.get("assignments", []))

    ann_keys = [f"{a['date']}|{a['title']}" for a in announcements]
    asg_keys = [str(x) for x in assignments]

    new_ann = [a for a, k in zip(announcements, ann_keys) if k not in seen_ann]
    new_asg = [a for a, k in zip(assignments, asg_keys) if k not in seen_asg]

    state[slug] = {"announcements": ann_keys, "assignments": asg_keys}
    _save(state)

    if first_run and first_run_silent:
        return {"announcements": [], "assignments": [], "first_run": True}
    return {"announcements": new_ann, "assignments": new_asg, "first_run": False}


def summarise(per_course: dict, index_changed: int = 0) -> str:
    """Human-readable 'what changed' block for the end of a sync."""
    lines, any_news = [], False
    for slug, d in sorted(per_course.items()):
        bits = []
        if d.get("first_run"):
            bits.append("first sync — everything indexed")
        for a in d.get("announcements", []):
            bits.append(f"announcement: {a['date']} — {a['title']}")
        for name in d.get("assignments", []):
            bits.append(f"new assignment: {name}")
        for f in d.get("files", []):
            bits.append(f"new file: {f}")
        if bits:
            any_news = True
            lines.append(f"  {slug}")
            lines += [f"    - {b}" for b in bits]

    if not any_news and not index_changed:
        return "No changes since last sync."
    head = "What's new:" if any_news else "No new course content."
    tail = (f"  index: {index_changed} chunk(s) updated" if index_changed else "")
    return "\n".join([head, *lines, tail]).rstrip()
