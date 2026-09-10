#!/usr/bin/env python3
"""MCP server exposing your Canvas classes + concept vaults to an LLM client
(Claude Desktop / Claude Code / Gemini CLI). Runs over stdio.

    python -m canvas_vault.mcp_server

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
from . import chdir_root      # noqa: E402  (must run before relative data paths)
chdir_root()

# MCP stdio uses stdout for JSON-RPC — force library logging to stderr.
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
logging.getLogger("canvasapi").setLevel(logging.WARNING)

from mcp.server import MCPServer

from . import canvas
from .course import Course

MAX_TEXT = 2000        # per-chunk cap: keep tool results well under client size limits


def grades_enabled() -> bool:
    """Whether to expose the grades tool over MCP. Off unless explicitly enabled.

    Everything else here is course material. Grades are a different sensitivity
    class, and exposing them means handing them to whichever LLM client is
    connected. That should be a decision someone makes, not one they inherit
    from a default, so this is opt-in rather than opt-out.

    Only the MCP surface is gated. `python -m canvas_vault.canvas grades` and
    Course.grades() stay available, because running a local CLI on your own
    machine sends nothing anywhere.
    """
    from dotenv import load_dotenv
    load_dotenv()
    return os.getenv("CANVAS_ENABLE_GRADES", "").strip().lower() in {
        "1", "true", "yes", "on"}

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


def _writable(path="index") -> bool:
    """Whether this process can actually write the vault.

    Claude Desktop launches this server through a sandboxed helper, and on macOS
    ~/Documents is TCC-protected, so a repo living there is readable but not
    writable from the client while the CLI in a terminal works fine. SQLite
    reports that as "attempt to write a readonly database", which reads like
    corruption and is not.
    """
    import tempfile
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path):
            return True
    except OSError:
        return False


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


if grades_enabled():
    @server.tool()
    def grades(course: str | None = None, items: bool = True) -> list[dict]:
        """The student's current grades, live from Canvas — ALL classes unless
        `course` is given. Never computed here, only reported.

        Each class returns `current_score` (graded work only) and `final_score`
        (ungraded counted as zero); quote whichever the question is actually about
        and say which one it is, because early in a term they differ a lot. `items`
        adds per-assignment scores, including work submitted but not marked yet.

        A class whose instructor hides grades comes back with an `error` field and
        no scores. Say so rather than treating it as a zero or as missing work.
        """
        rows = [_resolve(course).grades(items)] if course else canvas.grades(items=items)
        for r in rows:
            r["items"] = r.get("items", [])[:40]
        return rows
else:
    # Say so on stderr. A silently absent tool looks like a bug to anyone
    # wondering why their client cannot see their grades.
    print("canvas: grades tool not exposed (set CANVAS_ENABLE_GRADES=1 in .env "
          "to allow grades over MCP)", file=sys.stderr)


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
    from . import chat
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
    """Check Canvas for anything new: announcements, assignments, and newly
    posted files. Takes a few seconds. Use it when the student asks whether
    anything was posted recently, or when an answer might be stale.

    New lecture FILES are detected and named, but not transcribed — reading a
    slide deck needs a vision model and takes minutes, which would exceed this
    request's timeout. So a deck can be reported as posted while its contents
    are not yet searchable; the scheduled daily sync (or
    `python -m canvas_vault.sync`) transcribes it. Report a file as posted if it
    appears here, even though search_notes can't read it yet.
    """
    if not _writable():
        return ("Cannot write the local vault from this process, so nothing was "
                "refreshed. This is a filesystem permission, not a corrupt index: "
                "on macOS a repo under ~/Documents is TCC-protected and the client "
                "that launched this server may not have write access, while the "
                "same command works in a terminal. Either grant that app access to "
                "your Documents folder, move the repo outside ~/Documents, or run "
                "`python -m canvas_vault.sync` yourself. Canvas was NOT checked, so "
                "treat 'no new files' as unverified.")

    import contextlib
    import io

    from . import sync
    from . import updates as updates_mod
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
    return (f"Checked {', '.join(c.slug for c in courses)} — announcements, "
            f"assignments and file listings (new files are named but not yet "
            f"transcribed).\n{summary}\n\n" + "\n".join(latest))


if __name__ == "__main__":
    server.run()
