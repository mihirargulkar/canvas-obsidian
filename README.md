# Canvas Knowledge Graph & Study Assistant

Local, single-user tool that syncs Canvas course materials, builds an Obsidian-style
concept graph from lecture slides, and lets you study them with an LLM — grounded in
your own notes, deadlines, and announcements.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create `.env` (gitignored):

```
CANVAS_URL=https://<your-school>.instructure.com
CANVAS_TOKEN=<your Canvas access token>
GEMINI_API_KEY=<your Gemini API key>     # ingestion only; free tier is fine
```

## Build the vault (run per course; re-run to refresh)

```bash
.venv/bin/python ingest.py 253025      # course files -> markdown notes (Gemini vision)
.venv/bin/python extract.py            # notes -> concept graph -> vault/
.venv/bin/python updates.py 253025     # announcements + syllabus -> vault/updates/ + notes/
.venv/bin/python dashboard.py 253025   # vault/Dashboard.md: upcoming + recent announcements
.venv/bin/python chat.py index         # build the semantic search index
```

`ingest.py`, `extract.py`, and `updates.py` cache by content hash / write idempotently,
so re-runs are cheap and resumable (safe after a Gemini rate-limit).

The result is a real **Obsidian vault** in `vault/` — open the folder in Obsidian for
the graph, backlinks, `Dashboard.md`, and full announcement/syllabus text.

## Study with an LLM — Vault + MCP (recommended)

`mcp_server.py` exposes your vault + **live** Canvas to any MCP client (Claude
Desktop, Claude Code, Gemini CLI) as tools: `upcoming_assignments`, `announcements`,
`syllabus`, `search_notes` (semantic search over your slides), and `concept`. With a
Claude subscription or the free Gemini CLI, this costs nothing extra — no API metering.

**Claude Code** (this repo): `.mcp.json` is already here — Claude Code picks it up.

**Claude Desktop:** add to `~/Library/Application Support/Claude/claude_desktop_config.json`
(use absolute paths — Claude Desktop doesn't run in the repo dir):

```json
{
  "mcpServers": {
    "canvas": {
      "command": "/Users/mihirargulkar/Documents/PROJECTS/canvas-obsidian/.venv/bin/python",
      "args": ["/Users/mihirargulkar/Documents/PROJECTS/canvas-obsidian/mcp_server.py"]
    }
  }
}
```

Restart Claude Desktop, then ask e.g. *"what's due this week?"*, *"what's on the
midterm 2 study guide?"*, or *"explain gradient descent from my lecture notes"* — it
routes to the right tool and cites your material. The Canvas token stays in `.env`;
it is never passed through MCP.

## Web app (legacy / optional)

An earlier custom web UI still exists:

```bash
.venv/bin/python server.py      # http://localhost:8000
```

Superseded by the Vault + MCP path above, but kept for reference.
