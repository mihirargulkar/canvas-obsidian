#!/usr/bin/env python3
"""MCP server exposing the DS4400 concept vault + live Canvas to an LLM client
(Claude Desktop / Claude Code / Gemini CLI). Runs over stdio.

    python mcp_server.py

Wire into Claude Code via .mcp.json (in this repo) or Claude Desktop via its
claude_desktop_config.json — see README. The Canvas token is read from .env
by canvas.get_client(); it is never passed through MCP.
"""
import logging
import os
import sys
from pathlib import Path

# A client (Claude Desktop) launches us from an arbitrary CWD; anchor to the repo
# so .env, notes/, vault/, and chroma_db/ (all relative) resolve.
os.chdir(Path(__file__).resolve().parent)

# MCP stdio uses stdout for JSON-RPC — force all library logging to stderr and
# silence canvasapi's INFO request logs so they can't corrupt the protocol.
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
logging.getLogger("canvasapi").setLevel(logging.WARNING)

from mcp.server import MCPServer

import canvas
import extract
import updates

COURSE_ID = 253025  # DS4400 — single-course scope for now

server = MCPServer(
    name="canvas-ds4400",
    version="0.1.0",
    instructions=("Live Canvas data and the student's DS4400 machine-learning "
                  "concept vault. Use upcoming_assignments for deadlines "
                  "(deterministic), search_notes to ground conceptual answers "
                  "in the student's own lecture material, and announcements for "
                  "recent professor updates."),
)


@server.tool()
def upcoming_assignments(days: int = 7) -> list[dict]:
    """Assignments due within the next `days` days, live from Canvas. Deterministic —
    use this for any 'what's due' / deadline question rather than guessing."""
    return [{"due": d.isoformat(), "course": c, "name": n, "points": p}
            for d, c, n, p in canvas.upcoming(days)]


@server.tool()
def announcements(limit: int = 10) -> list[dict]:
    """Recent Canvas announcements (professor updates: class cancellations, exam
    logistics, study guides, posted solutions), newest first."""
    return updates.fetch_updates(COURSE_ID)["announcements"][:limit]


@server.tool()
def syllabus() -> str:
    """The course syllabus as plain text (dates, schedule, grading, policies)."""
    return updates.fetch_updates(COURSE_ID)["syllabus"]


@server.tool()
def search_notes(query: str, k: int = 5) -> list[dict]:
    """Semantic search over the student's own lecture notes. Returns matching
    passages each with its source lecture and section, for grounded citation."""
    import chat
    res = chat._collection().query(query_texts=[query], n_results=k)
    docs, metas = res["documents"][0], res["metadatas"][0]
    return [{"source": m["source"], "section": m["section"], "text": d}
            for d, m in zip(docs, metas)]


@server.tool()
def concept(name: str) -> dict | None:
    """Look up one concept from the knowledge graph: its definition, linked
    concepts, and which lectures it appears in. None if not found."""
    return extract.concept_data(name)


if __name__ == "__main__":
    server.run()
