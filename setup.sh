#!/usr/bin/env bash
# One-shot setup: virtualenv, dependencies, .env, and (optionally) a first sync.
#
#   ./setup.sh                 # interactive
#   ./setup.sh --no-sync       # set up but don't run the first sync
#
# Safe to re-run: it skips whatever is already in place.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
RUN_SYNC=1
[[ "${1:-}" == "--no-sync" ]] && RUN_SYNC=0

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }

# --- Python -------------------------------------------------------------------
say "Checking Python"
PY_BIN="$(command -v python3 || true)"
[[ -n "$PY_BIN" ]] || { echo "python3 not found. Install Python 3.10+ and re-run."; exit 1; }
"$PY_BIN" - <<'EOF' || { echo "  Python 3.10+ required."; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
EOF
ok "$("$PY_BIN" -V)"

# --- virtualenv + dependencies ------------------------------------------------
say "Installing dependencies"
[[ -d .venv ]] || "$PY_BIN" -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt
ok "virtualenv ready at .venv (torch comes with sentence-transformers, so this is the slow part)"

# --- LibreOffice (needed to turn .pptx/.docx into PDF for transcription) -------
say "Checking LibreOffice"
if ./.venv/bin/python -c "
import sys; sys.path.insert(0, '.')
from canvas_vault.ingest import find_soffice
sys.exit(0 if find_soffice() else 1)" 2>/dev/null; then
  ok "found: $(./.venv/bin/python -c "import sys;sys.path.insert(0,'.');from canvas_vault.ingest import find_soffice;print(find_soffice())")"
else
  warn "not found — PDFs still work, but .pptx/.docx slides can't be transcribed."
  warn "macOS: brew install --cask libreoffice   Debian/Ubuntu: sudo apt install libreoffice"
  warn "Already installed elsewhere? Set SOFFICE=/path/to/soffice"
fi

# --- credentials --------------------------------------------------------------
say "Credentials"
if [[ -f .env ]]; then
  ok ".env already exists — leaving it alone"
else
  cp .env.example .env
  echo "  Two values are needed. Both stay in .env on this machine and are gitignored."
  echo
  # No default. This used to default to the author's own university, so anyone
  # else pressing Enter silently configured the wrong institution and got auth
  # errors with nothing pointing at the cause.
  echo "  Canvas URL — your school's Canvas, e.g. https://yourschool.instructure.com"
  while :; do
    read -rp "  Canvas URL: " CANVAS_URL
    CANVAS_URL="${CANVAS_URL%/}"
    [[ "$CANVAS_URL" == https://*.* ]] && break
    echo "    needs to be a full https:// URL"
  done
  echo "  Canvas token — Canvas > Account > Settings > New Access Token"
  read -rsp "  Canvas token (hidden): " CANVAS_TOKEN; echo
  echo "  Gemini key (free tier) — https://aistudio.google.com/app/apikey"
  read -rsp "  Gemini API key (hidden): " GEMINI_API_KEY; echo

  "$PY_BIN" - "$CANVAS_URL" "$CANVAS_TOKEN" "$GEMINI_API_KEY" <<'EOF'
import pathlib, sys
url, token, key = sys.argv[1:4]
p = pathlib.Path(".env")
out = []
for line in p.read_text().splitlines():
    if line.startswith("CANVAS_URL="):        line = f"CANVAS_URL={url}"
    elif line.startswith("CANVAS_TOKEN="):    line = f"CANVAS_TOKEN={token}"
    elif line.startswith("GEMINI_API_KEY="):  line = f"GEMINI_API_KEY={key}"
    out.append(line)
p.write_text("\n".join(out) + "\n")
EOF
  ok "wrote .env"
fi

# --- verify the token actually works before spending an hour syncing ----------
say "Verifying Canvas access"
if ./.venv/bin/python -m canvas_vault.sync --list 2>/dev/null; then
  ok "Canvas token works"
else
  warn "Could not list your classes. Check CANVAS_URL and CANVAS_TOKEN in .env, then re-run."
  exit 1
fi

# --- first sync ---------------------------------------------------------------
if [[ "$RUN_SYNC" == "1" ]]; then
  say "First sync"
  echo "  This transcribes your slide decks through a vision model. It can take"
  echo "  tens of minutes and may hit Gemini's free daily quota — it's resumable,"
  echo "  so just run it again tomorrow and it picks up where it stopped."
  read -rp "  Run it now? [Y/n]: " GO
  if [[ ! "${GO:-Y}" =~ ^[Nn] ]]; then
    ./.venv/bin/python -m canvas_vault.sync
  else
    warn "skipped — run: .venv/bin/python -m canvas_vault.sync"
  fi
fi

say "Done"
cat <<'EOF'
  Study with an LLM (MCP):
    Claude Code   — .mcp.json is already here, just open this repo
    Claude Desktop— see the config block in README.md, then quit and reopen it
  Browse the vault:
    open the vault/ folder as an Obsidian vault
  Keep it current:
    tools/install-daily-sync.sh      # macOS: sync every morning
EOF
