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

## Sync all your classes

One command syncs every class you're enrolled in this term:

```bash
.venv/bin/python sync.py               # all current classes
.venv/bin/python sync.py --list        # show detected classes, change nothing
.venv/bin/python sync.py --only DS4400 # just one class
```

Per class it runs: **ingest** (files + homework → markdown via Gemini vision) →
**updates** (announcements + syllabus) → **extract** (concept graph) →
**dashboard**, then builds one course-tagged search index. Everything is
content-hash cached, so re-runs are cheap and resumable after a rate-limit.

Individual steps are still available (`ingest.py <id>`, `extract.py <SLUG>`,
`updates.py <id>`, `dashboard.py`, `chat.py index`).

### Layout

```
notes/<SLUG>/          transcribed markdown (lectures, hw-*, code-*, announcements)
vault/Dashboard.md     deadlines across ALL classes
vault/<SLUG>/          concepts/ lectures/ updates/ Dashboard.md   <- open in Obsidian
```

`Course` (in `course.py`) is the domain object — it owns a class's id, slug and
paths and exposes the pipeline as `course.sync()`, so nothing threads a slug
through by hand.

## Study with an LLM — Vault + MCP (recommended)

`mcp_server.py` exposes your classes + **live** Canvas to any MCP client (Claude
Desktop, Claude Code, Gemini CLI) as tools: `list_courses`, `upcoming_assignments`,
`announcements`, `syllabus`, `search_notes` (semantic search over your slides,
homework and notebooks), and `concept`. Every tool takes an optional `course` slug —
omit it to span all your classes. With a Claude subscription or the free Gemini CLI,
this costs nothing extra — no API metering.

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

