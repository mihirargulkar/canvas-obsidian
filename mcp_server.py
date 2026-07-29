#!/usr/bin/env python3
"""MCP server exposing your Canvas classes + concept vaults to an LLM client
(Claude Desktop / Claude Code / Gemini CLI). Runs over stdio.

    python mcp_server.py

Multi-class: every tool takes an optional `course` slug (e.g. "DS4400").
Omit it to span all your current classes. The Canvas token is read from .env;
it is never passed through MCP.
"""
import logging
import os
import sys
from pathlib import Path

# A client launches us from an arbitrary CWD; anchor to the repo so .env,
# notes/, vault/, and chroma_db/ (all relative) resolve.
os.chdir(Path(__file__).resolve().parent)

# MCP stdio uses stdout for JSON-RPC — force library logging to stderr.
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
logging.getLogger("canvasapi").setLevel(logging.WARNING)

from mcp.server import MCPServer

import canvas
from course import Course

MAX_TEXT = 2000        # per-chunk cap: keep tool results well under client size limits

server = MCPServer(
    name="canvas",
    version="0.2.0",
    instructions=("The student's Canvas classes and their concept vaults. Use "
                  "list_courses to see their classes; upcoming_assignments for "
                  "deadlines (deterministic, all classes by default); search_notes "
                  "to ground conceptual answers in their own lecture material, "
                  "homework and notebooks; announcements for professor updates. "
                  "Every tool takes an optional `course` slug to focus one class."),
)


def _resolve(slug: str) -> Course:
    for c in Course.current():
        if c.slug.lower() == slug.lower():
            return c
    raise ValueError(f"no current course with slug {slug!r}")


@server.tool()
def list_courses() -> list[dict]:
    """The student's current classes this term (slug, canvas id, full name).
    Call this first when a question is class-specific but the class is unclear."""
    return [{"slug": c.slug, "id": c.id, "name": c.name} for c in Course.current()]


@server.tool()
def upcoming_assignments(days: int = 7, course: str | None = None) -> list[dict]:
    """Assignments due within `days`, live from Canvas — across ALL classes unless
    `course` is given. Deterministic: use for any deadline question."""
    rows = _resolve(course).upcoming(days) if course else canvas.upcoming(days)
    return [{"due": d.isoformat(), "course": c, "name": n, "points": p}
            for d, c, n, p in rows]


@server.tool()
def announcements(course: str, limit: int = 10) -> list[dict]:
    """Recent Canvas announcements for one class (cancellations, exam logistics,
    study guides, posted solutions), newest first."""
    return _resolve(course).announcements(limit)


@server.tool()
def syllabus(course: str) -> str:
    """One class's syllabus as plain text (dates, schedule, grading, policies)."""
    return _resolve(course).syllabus()[:20000]


@server.tool()
def search_notes(query: str, k: int = 5, course: str | None = None) -> list[dict]:
    """Semantic search over the student's own course material — lecture slides,
    homework prompts, and code notebooks. Searches ALL classes unless `course` is
    given. Each hit carries its course, source file and section for citation."""
    import chat
    where = {"course": course} if course else None
    res = chat._collection().query(query_texts=[query], n_results=k, where=where)
    docs, metas = res["documents"][0], res["metadatas"][0]
    return [{"course": m["course"], "source": m["source"], "section": m["section"],
             "text": d[:MAX_TEXT]} for d, m in zip(docs, metas)]


@server.tool()
def concept(name: str, course: str) -> dict | None:
    """Look up one concept in a class's knowledge graph: definition, linked
    concepts, and which lectures it appears in. None if not found."""
    return _resolve(course).concept(name)


@server.tool()
def refresh(course: str | None = None) -> str:
    """Re-read announcements, assignments and the syllabus from Canvas, then
    report what's new since the last check. Takes a few seconds. Use it when the
    student asks whether anything was posted recently, or when an answer might be
    stale.

    Does NOT transcribe newly-posted lecture files — that needs a vision model
    and takes minutes, which would exceed this request's timeout. New slides are
    picked up by the scheduled daily sync (or `python sync.py` in a terminal).
    """
    import io, contextlib, sync, updates as updates_mod
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):        # keep sync logs off MCP stdout
        courses, summary = sync.run_sync(only=course, deep=False)

    # Always report the CURRENT latest items, not just the diff. "Nothing new
    # since last sync" is only meaningful to a caller who already knows the
    # current state — one with stale context would otherwise conclude its old
    # view is still current and confidently report an outdated announcement as
    # the most recent.
    latest = []
    for c in courses:
        try:
            anns = updates_mod.fetch_updates(c.id).get("announcements", [])[:3]
        except Exception:
            continue
        if anns:
            latest.append(f"{c.slug} — most recent announcements now:")
            latest += [f"  {a['date']}  {a['title']}" for a in anns]
    return (f"Checked {', '.join(c.slug for c in courses)} "
            f"(announcements/assignments only).\n{summary}\n\n" + "\n".join(latest))


if __name__ == "__main__":
    server.run()
