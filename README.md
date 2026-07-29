# Canvas Knowledge Graph & Study Assistant

Turn your Canvas courses into a **local Obsidian vault** — lecture slides transcribed
to markdown, a cross-lecture concept graph, homework prompts, announcements and
syllabus — then study it with Claude or Gemini through MCP, grounded in your own
material with citations.

Local-first: everything lands as plain markdown you own, on your machine.

## Why this, when Canvas AI tools already exist?

Honest positioning — several mature tools already overlap with parts of this:

- **Canvas API MCP servers** exist that wrap the Canvas API far more completely
  (90+ tools, including grading and instructor features). If all you want is to
  *talk to Canvas* from an LLM, use one of those — the live-Canvas tools here are
  deliberately thin.
- **Hosted study assistants** do RAG over your course files, and **concept-map
  generators** turn uploaded PDFs into diagrams. Both are cloud SaaS: your material
  lives on their servers.

What this does that they don't:

- **Builds a real Obsidian vault** — plain `.md` with `[[wikilinks]]`, a concept graph
  merged **across lectures** (a concept links to every lecture it appears in), plus
  backlinks and graph view for free.
- **Local-first, you own the data.** Nothing is hosted; the markdown outlives this tool.
- **Free on a subscription you already have** — the vault is exposed over MCP, so
  Claude (Pro/Max) or the free Gemini CLI is the chat layer. No per-query API metering.
- **One index across all your classes**, with homework prompts and code notebooks in it.

If you want a polished hosted product, use the SaaS. If you want your course knowledge
as files you keep, this.

## Requirements

- Python 3.10+
- **LibreOffice** — converts `.pptx`/`.docx` to PDF for transcription
  (macOS `brew install --cask libreoffice`, Debian/Ubuntu `apt install libreoffice`).
  Auto-detected on `$PATH`; override with `SOFFICE=/path/to/soffice`.
- A **Canvas API token** (Canvas → Account → Settings → New Access Token)
- A **Gemini API key** ([free tier](https://aistudio.google.com/app/apikey)) — used only
  to transcribe slides and extract concepts

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # then fill it in
```

`.env` (gitignored — never commit it):

```
CANVAS_URL=https://<your-school>.instructure.com
CANVAS_TOKEN=<your Canvas access token>
GEMINI_API_KEY=<your Gemini API key>
# CANVAS_TZ=America/New_York   # optional; defaults to your Canvas account timezone
```

## Sync your classes

```bash
.venv/bin/python sync.py               # every class you're taking this term
.venv/bin/python sync.py --list        # show detected classes, change nothing
.venv/bin/python sync.py --only CS101  # just one class
```

Per class: **ingest** (files + homework → markdown via Gemini vision) → **updates**
(announcements + syllabus) → **extract** (concept graph) → **dashboard**, then one
course-tagged search index.

> **First run takes a while** (tens of MB of slide decks through a vision model) and
> may hit Gemini's free-tier daily quota. It is resumable — everything is content-hash
> cached, so just run it again; finished files cost nothing and aren't re-downloaded.
> A class whose Files tab the instructor disabled degrades to assignments-only rather
> than failing.

Individual steps still work: `ingest.py <course_id>`, `extract.py <SLUG>`,
`updates.py <course_id>`, `dashboard.py`, `chat.py index`.

### Layout

```
notes/<SLUG>/          transcribed markdown (lectures, hw-*, code-*, announcements)
vault/Dashboard.md     deadlines across ALL classes
vault/<SLUG>/          concepts/ lectures/ updates/ Dashboard.md   <- open in Obsidian
```

Open the `vault/` folder as an Obsidian vault for the graph, backlinks and dashboards.

## Study with an LLM — Vault + MCP

`mcp_server.py` exposes your classes + live Canvas to any MCP client (Claude Desktop,
Claude Code, Gemini CLI): `list_courses`, `upcoming_assignments`, `announcements`,
`syllabus`, `search_notes` (semantic search over your slides, homework and notebooks),
and `concept`. Every tool takes an optional `course` slug — omit it to span all classes.

### Connecting a client

Every MCP client takes the **same server definition** — only the file it lives in
differs. Use **absolute paths**: clients don't run in the repo directory.

```json
{
  "mcpServers": {
    "canvas": {
      "command": "/absolute/path/to/canvas-obsidian/.venv/bin/python",
      "args": ["/absolute/path/to/canvas-obsidian/mcp_server.py"]
    }
  }
}
```

| Client | Where that block goes |
|---|---|
| **Claude Code** | `.mcp.json` — already in this repo, picked up automatically |
| **Claude Desktop** | macOS `~/Library/Application Support/Claude/claude_desktop_config.json` · Windows `%APPDATA%\Claude\claude_desktop_config.json` · Linux `~/.config/Claude/claude_desktop_config.json` |
| **Gemini CLI** | `~/.gemini/settings.json` (or `.gemini/settings.json` per project) |
| **Cursor** | `.cursor/mcp.json` in the project (or `~/.cursor/mcp.json` globally) |
| **VS Code / Copilot** | `.vscode/mcp.json` — note VS Code nests servers under `"servers"` rather than `"mcpServers"` |
| **Continue** | `.continue/mcpServers/mcp.json` |

Merge into the existing `mcpServers` object if the file already has one — don't
overwrite it. Restart the client afterwards; most only read config at launch.

**A note on Groq, OpenAI and other providers:** MCP is a *client* protocol, not a model
API. Groq, OpenAI and Together are inference providers — they don't connect to MCP
servers themselves. To use those models with this server, point an MCP-capable client
that supports custom providers (Continue, Cline, LibreChat, Goose) at them; the server
definition above is unchanged. The zero-extra-cost paths are Claude (Pro/Max
subscription) and the Gemini CLI free tier.

Then ask *"what's due this week?"*, *"what's on the midterm 2 study guide?"*, or
*"explain gradient descent from my lecture notes"* — the client picks the right tool
and cites your material.

## Privacy, cost and terms

- **Your Canvas token stays in `.env`**, is read only by this tool, and is never sent
  through MCP or to any model.
- **Course content is sent to Google's Gemini API** during ingestion (slides, homework
  prompts) to transcribe it. If that's not acceptable for your material, don't ingest it.
- **Self-hosted, single-user by design.** Instructure's API terms prohibit sharing your
  token with third parties — running this yourself with your own token is fine; offering
  it as a hosted service for other students is not.
- Ingestion costs $0 on Gemini's free tier (slower); chat costs $0 on a Claude
  subscription or the free Gemini CLI.

## Development

```bash
.venv/bin/python -m pytest -q
```

Tests that need a built vault skip automatically. `eval_graph.py` is a **development**
tool: it scores concept-graph quality against a hand-labelled gold set for one specific
course, so it is not meaningful for other courses as-is.

## License

MIT — see [LICENSE](LICENSE).
