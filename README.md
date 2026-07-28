# Canvas Knowledge Graph & Study Assistant

Local, single-user tool that syncs Canvas course materials, builds an Obsidian-style
concept graph from lecture slides, and answers questions grounded in your own notes.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create `.env` (gitignored):

```
CANVAS_URL=https://<your-school>.instructure.com
CANVAS_TOKEN=<your Canvas access token>
GEMINI_API_KEY=<your Gemini API key>
```

## Pipeline (run once per course, re-run to refresh)

```bash
.venv/bin/python canvas.py due --days 14      # deterministic deadlines (no LLM)
.venv/bin/python ingest.py 253025             # course files -> markdown notes (Gemini vision)
.venv/bin/python extract.py                   # notes -> concept graph -> vault/
.venv/bin/python chat.py index                # build the vector index
```

Both `ingest.py` and `extract.py` cache by content hash, so re-runs are cheap and
resumable (safe to re-run after a Gemini rate-limit).

## Web app (Phase 5)

```bash
.venv/bin/python server.py                    # http://localhost:8000
```

Chat is home; toggle to **Graph** to explore the concept map (click a node → note
panel → "Ask about this"). Upcoming deadlines are always in the sidebar. Deadline
questions are answered deterministically from Canvas; conceptual questions are
answered from your slides with citations.

The `vault/` folder is also a real Obsidian vault — open it directly for the built-in
graph/backlinks view.
