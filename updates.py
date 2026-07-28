#!/usr/bin/env python3
"""Fetch Canvas announcements + syllabus, strip HTML, and write them into the
vault (Obsidian-browsable) and the note index (semantic-searchable).

    python updates.py 253025

Announcements are the time-stamped 'key updates' professors post (cancellations,
exam logistics, study guides). Kept out of the concept graph (they're not
concepts) — see extract.is_lecture.
"""
import argparse
import html
import re
from pathlib import Path

NOTES = Path("notes")
VAULT = Path("vault")


def strip_html(s):
    """Crude HTML -> readable text. ponytail: regex strip; swap for a parser if layout matters."""
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", s)   # drop css/js blocks
    s = re.sub(r"(?i)<li[^>]*>", "\n- ", s)
    s = re.sub(r"(?i)</(p|div|h[1-6]|tr|li)\s*>", "\n", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n[ \t]*(\n[ \t]*)+", "\n\n", s)
    return s.strip()


def fetch_updates(course_id):
    """Live from Canvas -> {'syllabus': str, 'announcements': [{date,title,body}]}."""
    from canvas import get_client
    course = get_client().get_course(course_id, include=["syllabus_body"])
    syllabus = strip_html(getattr(course, "syllabus_body", "") or "")
    anns = []
    for a in course.get_discussion_topics(only_announcements=True):
        anns.append({"date": (getattr(a, "posted_at", "") or "")[:10],
                     "title": getattr(a, "title", "(untitled)"),
                     "body": strip_html(getattr(a, "message", "") or "")})
    anns.sort(key=lambda x: x["date"], reverse=True)
    return {"syllabus": syllabus, "announcements": anns}


def _chunks(text, words=150):
    w = text.split()
    return [" ".join(w[i:i + words]) for i in range(0, len(w), words)] or [""]


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def write_notes(slug, data):
    """Write announcements.md + syllabus.md into notes/<slug>/ (for the search
    index) and vault/updates/ (for Obsidian + filesystem MCP)."""
    ann_md = [f"# {slug} Announcements", ""]
    for a in data["announcements"]:
        ann_md += [f"## {a['date']} — {a['title']}", a["body"], ""]
    ann_md = "\n".join(ann_md)

    syl_md = [f"# {slug} Syllabus", ""]
    for i, ch in enumerate(_chunks(data["syllabus"]), 1):
        syl_md += [f"## Syllabus (part {i})", ch, ""]
    syl_md = "\n".join(syl_md)

    for base in (NOTES / slug, VAULT / "updates"):
        _write(base / "announcements.md", ann_md)
        _write(base / "syllabus.md", syl_md)


def main():
    p = argparse.ArgumentParser(description="Fetch Canvas announcements + syllabus")
    p.add_argument("course_id", type=int)
    p.add_argument("--slug", default="DS4400")
    a = p.parse_args()
    data = fetch_updates(a.course_id)
    write_notes(a.slug, data)
    print(f"{a.slug}: {len(data['announcements'])} announcements, "
          f"syllabus {len(data['syllabus'])} chars -> notes/{a.slug}/ + vault/updates/")


if __name__ == "__main__":
    main()
