# Canvas Study Assistant — Web App (Phase 5) Design

**Date:** 2026-07-27 · **Status:** Draft for review

## Context

Phases 1–4 shipped a working study assistant as CLI tools over a Python pipeline:
`canvas.py` (deterministic due dates), `ingest.py` (files → markdown), `extract.py`
(concept graph → Obsidian vault), `chat.py` (RAG chat with a deadline/semantic
router). The value is real but the UX is split: the graph lives in Obsidian, chat
and deadlines live in the terminal. Phase 5 unifies them into one local web app so
the user sees deadlines and can chat/explore without switching tools.

This replaces Obsidian as the *primary* UI. The vault stays (it's still the graph's
data source and remains Obsidian-openable), but day-to-day use moves to the app.

## Goals

- One local app: chat is home; a Graph view is one click away; deadlines are always visible.
- Reuse the existing, already-verified Python — the web layer is a thin wrapper, not a rewrite.
- No build step, minimal new dependencies.

## Non-goals

- Multi-user / hosted / auth — localhost, single user, same as the rest of the project.
- New retrieval/graph logic — the app surfaces what Phases 1–4 already produce.
- Real-time Canvas push — deadlines are fetched live per page load, not streamed.

## Architecture

Single **FastAPI** app (`server.py`) serving a **static frontend** (`web/`), both in
the existing repo/venv. Vanilla JS frontend — no React/Vite, no bundler.

```
Browser (web/index.html + app.js + styles.css)
   │  fetch()
   ▼
FastAPI (server.py)  ── wraps existing Python:
   GET  /api/due?days=7        -> canvas.py  (current_courses + in_window)
   GET  /api/graph             -> vault/concepts/*.md  -> {nodes, edges}
   GET  /api/concept/{name}    -> one concept note -> {definition, links, lectures}
   POST /api/ask {q}           -> chat.py router (semantic OR deadline)
   GET  /                      -> web/index.html (+ static)
```

Run: `uvicorn server:app` (or `python server.py`), open `http://localhost:8000`.

### Reuse & required refactors (small, in existing files)

- **`chat.py`**: `answer_semantic` / `answer_structured` currently *print*. Refactor
  each to *return* a result dict (`{mode, answer, sources}`); `cmd_ask` prints the
  returned dict so the CLI is unchanged. `server.py`'s `/api/ask` returns it as JSON.
- **`canvas.py`**: extract the due-row collection into `upcoming(days) -> [(due, course, name)]`;
  `cmd_due` formats it (CLI unchanged), `/api/due` serializes it.
- **graph JSON**: add `graph_data()` (reads `vault/concepts/*.md`, returns nodes with
  `{id, lect, degree}` + edges) — the logic already prototyped during Phase 3. Lives
  in `extract.py` (it owns the vault) and is imported by `server.py`.

No change to `ingest.py`, `eval_graph.py`, the vault format, or Chroma.

## Frontend components

Single page, two views toggled client-side; the sidebar is always present.

- **Sidebar (persistent):** segmented **Chat / Graph** toggle; **Upcoming** section
  (cards from `/api/due`, item due today flagged amber); **Recent concepts** section
  (last N concepts opened/asked, stored in `localStorage` — no backend state).
- **Chat view (home):** conversation area (user = subtle bubble, assistant = plain
  text) + citation chips under answers; dark input pill with coral send. `POST /api/ask`;
  render markdown with `marked`; render LaTeX with KaTeX (answers contain `$…$`).
- **Graph view:** `force-graph` (CDN) canvas over `/api/graph`, nodes colored by
  lecture. Click a node → fetch `/api/concept/{name}` → **note panel** (definition,
  `[[links]]` as clickable nodes, "In: L4, L5") + **"Ask about this →"** button that
  seeds the chat input, switches to Chat, and sends.

### Visual design (locked)

Fully dark, Claude Code desktop aesthetic (per approved mockup): near-black top bar,
`#1e1d1b` sidebar / `#262523` main, off-white text, coral (`#d97757`) accent, amber
(`#d9a441`) for urgency, dim uppercase section labels, colored concept dots, dark
rounded input pill. **No emojis** — text labels and simple dots/hairline glyphs only.
The Graph canvas uses Obsidian-style glowing nodes on the dark ground.

## Data flow: "Ask about this"

Graph node click → panel → button → `app.js` sets chat input to
`"Explain <concept> from my course material"`, switches view to Chat, calls the same
`/api/ask` path a typed question uses. One code path for all questions.

## Error handling

- **Gemini 429/5xx** on `/api/ask`: backend catches, returns `{mode, answer: "The
  model is rate-limited right now — try again shortly.", sources: []}` (HTTP 200) so
  the UI shows a message, not a crash. (Reuses the existing backoff first.)
- **Empty index / vault** (`/api/ask`, `/api/graph`): return an explanatory payload
  ("No indexed material yet — run ingest/extract/index") the UI renders inline.
- **Canvas fetch failure** (`/api/due`): return `[]` + a `warning` field the sidebar shows.
- Frontend `fetch` failures: inline error line in the relevant panel, never a blank screen.

## Testing / verification

- **Backend smoke test** (`test_server.py`, FastAPI `TestClient`): each endpoint
  returns the expected shape — `/api/due` rows have a real datetime+course+name;
  `/api/graph` has >0 nodes and edges; `/api/concept/{known}` returns a definition;
  `/api/ask` with a deadline query returns `mode=="deadline"`, with a concept query
  returns `mode=="semantic"` and non-empty `sources`. Reuses the live pipeline.
- **Existing checks still pass:** `test_canvas.py`, `eval_graph.py`.
- **Manual E2E** (Claude Browser MCP): load app → ask a concept question (see cited
  answer) → switch to Graph → click "Gradient Descent" → panel shows note + links →
  "Ask about this" returns to Chat with a cited answer → Upcoming list matches
  `python canvas.py due`.

## New dependencies

`fastapi`, `uvicorn` (backend). Frontend libs via CDN: `force-graph`, `marked`, `katex`.
No bundler, no new frontend package manager.

## Open decisions (defaults chosen; flag at review)

- **Recent concepts source:** last-opened via `localStorage` (default) vs top-degree
  concepts. Chose last-opened — it reflects what the student is actually studying.
- **Course scope:** DS4400 only for now (per earlier decision); `/api/graph` and the
  index read the DS4400 vault. Multi-course is a later, additive change.
