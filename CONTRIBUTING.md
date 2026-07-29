# Contributing

Thanks for taking a look. This is a small, local-first tool and contributions are
welcome.

## Scope

Portability across institutions and platforms, ingestion of more Canvas material,
concept-extraction quality, MCP tools, and bug fixes are all fair game.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # fill in your own Canvas token + Gemini key
```

## Tests

```bash
.venv/bin/python -m pytest -q
```

Tests that need a built vault skip themselves, so the suite runs on a fresh clone with
no credentials — that is what CI does. Keep it that way: **don't add tests that require
a live Canvas token or a populated vault without a `skipif` guard.** CI installs only
the light dependencies (`canvasapi`, `python-dotenv`, `google-genai`, `pytest`), because
`chromadb`/`sentence-transformers` pull ~2GB of torch. Keep those imports *inside*
functions so importing a module stays cheap.

## Code style

No framework, no ceremony. A few conventions that are load-bearing:

- **`Course` (in `course.py`) is the domain object.** If you find yourself threading a
  `slug` argument through a new call chain, add a method there instead.
- **Isolate failures per class and per step.** Instructors disable Canvas tabs all the
  time; one course returning HTTP 403 must never abort a whole sync.
- **Cache anything that costs money or bandwidth.** Ingestion and extraction are
  content-hash cached; Canvas reads are TTL-cached. Re-running should be nearly free.
- **Don't send more to a model than you need**, and cap tool results — MCP clients
  reject payloads over ~1MB.
- Comments explain *why*, not *what*. A `ponytail:` comment marks a deliberate shortcut
  and names its ceiling.

## Pull requests

Keep them focused, explain the user-visible effect, and say how you verified it. If a
change touches ingestion or extraction, mention roughly what it costs in model calls.
