#!/usr/bin/env bash
# Rebuild docs/PRD.pdf from docs/PRD.md.
#
#   tools/build-prd.sh
#
# docs/PRD.md is the source of truth; the PDF is a build artifact that happens
# to be committed, because "add a PRD pdf" is what people ask for. Edit the
# markdown, run this, commit both.
#
# Uses pandoc for markdown -> HTML and headless Chrome for HTML -> PDF.
# ponytail: no LaTeX, no weasyprint, no new dependency. Both are already here.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO/docs/PRD.md"
OUT="$REPO/docs/PRD.pdf"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

command -v pandoc >/dev/null || { echo "pandoc not found: brew install pandoc" >&2; exit 1; }

CHROME=""
for c in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
         "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" \
         "$(command -v google-chrome || true)" "$(command -v chromium || true)"; do
  [[ -x "$c" ]] && { CHROME="$c"; break; }
done
[[ -n "$CHROME" ]] || { echo "no Chrome/Chromium found (needed for HTML -> PDF)" >&2; exit 1; }

cat > "$TMP/prd.css" <<'CSS'
@page { size: letter; margin: 20mm 18mm 18mm 18mm; }
html { font-size: 10.5pt; }
body {
  font-family: "Charter", "Georgia", serif;
  color: #1a1a1a; line-height: 1.5; max-width: none; margin: 0;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
h1, h2, h3, .meta, code, th { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; }
h1 {
  font-size: 22pt; font-weight: 600; letter-spacing: -0.4pt;
  margin: 0 0 2pt; line-height: 1.15;
}
.subtitle {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 11pt; font-weight: 400; color: #6b6b6b;
  text-transform: uppercase; letter-spacing: 1.2pt; margin: 0 0 14pt;
}
.meta {
  font-size: 8.5pt; color: #444; line-height: 1.6;
  border-top: 2px solid #1a1a1a; border-bottom: 1px solid #d8d8d8;
  padding: 8pt 0; margin: 0 0 22pt;
}
.meta p { margin: 0; }
h2 {
  font-size: 13pt; font-weight: 600; margin: 22pt 0 7pt;
  padding-bottom: 3pt; border-bottom: 1px solid #d8d8d8;
  break-after: avoid; page-break-after: avoid;
}
h3 {
  font-size: 10.5pt; font-weight: 600; margin: 14pt 0 5pt; color: #333;
  break-after: avoid; page-break-after: avoid;
}
p { margin: 0 0 8pt; orphans: 2; widows: 2; }
ul, ol { margin: 0 0 8pt; padding-left: 18pt; }
li { margin-bottom: 3pt; }
strong { font-weight: 600; }
em { color: #444; }
table {
  border-collapse: collapse; width: 100%; margin: 8pt 0 14pt;
  font-size: 8.5pt; break-inside: avoid; page-break-inside: avoid;
}
th {
  text-align: left; font-weight: 600; font-size: 7.5pt;
  text-transform: uppercase; letter-spacing: 0.5pt;
  border-bottom: 1.5px solid #1a1a1a; padding: 5pt 7pt 4pt 0; vertical-align: bottom;
}
td { padding: 5pt 7pt 5pt 0; border-bottom: 1px solid #e6e6e6; vertical-align: top; }
tr:last-child td { border-bottom: none; }
td:first-child, th:first-child { padding-left: 0; }
code {
  font-family: "SF Mono", Menlo, monospace; font-size: 8.5pt;
  background: #f2f2f0; padding: 1pt 3pt; border-radius: 2px;
}
pre {
  background: #f7f7f5; border: 1px solid #e2e2e0; border-left: 2.5px solid #999;
  padding: 9pt 11pt; font-size: 8pt; line-height: 1.45; overflow-x: hidden;
  break-inside: avoid; page-break-inside: avoid; margin: 8pt 0 14pt;
}
pre code { background: none; padding: 0; font-size: inherit; }
blockquote { margin: 8pt 0; padding-left: 11pt; border-left: 2px solid #ccc; color: #555; }
hr { border: none; border-top: 1px solid #d8d8d8; margin: 16pt 0; }
CSS

pandoc "$SRC" --standalone --from=markdown --to=html5 \
  --css=prd.css --output "$TMP/prd.html"

"$CHROME" --headless --disable-gpu --no-sandbox --no-pdf-header-footer \
  --virtual-time-budget=6000 \
  --print-to-pdf="$OUT" "file://$TMP/prd.html" 2>/dev/null

echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"
