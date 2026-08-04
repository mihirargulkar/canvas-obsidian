#!/usr/bin/env python3
"""Phase 2: ingest a course's files + homework into faithful markdown notes.

    python -m canvas_vault.ingest 253025            # files (slides, notebooks, docs) + homework
    python -m canvas_vault.ingest 253025 --limit 3  # first 3 files (cheap trial)

Vision files (pdf/pptx/docx) go through Gemini; text files (ipynb/txt/md) are
extracted directly (no model call). Homework = assignment descriptions + the
prompt PDFs linked in them. Content-hash cache: unchanged files never re-hit
Gemini; failed files retry next run. Non-lecture material (notebooks, homework,
assignments) is prefixed so extract.py keeps it OUT of the concept graph but it
stays in the search index.
"""
import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import time
from pathlib import Path

from google import genai
from dotenv import load_dotenv

from . import chdir_root
from . import ROOT
from .canvas import get_client, course_label

VISION_EXT = {".pdf", ".pptx", ".docx"}          # -> pdf -> Gemini vision
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}   # -> straight to Gemini (reads images natively)
TEXT_EXT = {".ipynb", ".txt", ".md"}             # -> extracted directly, no model
INGEST_EXT = VISION_EXT | IMAGE_EXT | TEXT_EXT
MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
SOFFICE_CANDIDATES = [                                   # checked after $PATH
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",          # macOS
    "/usr/bin/soffice", "/usr/bin/libreoffice",                      # Linux
    "/snap/bin/libreoffice",                                         # Linux (snap)
    r"C:\Program Files\LibreOffice\program\soffice.exe",             # Windows
]


def find_soffice():
    """Locate LibreOffice (used to convert pptx/docx -> pdf). $SOFFICE wins."""
    import shutil
    env = os.getenv("SOFFICE")
    if env and Path(env).exists():
        return env
    on_path = shutil.which("soffice") or shutil.which("libreoffice")
    if on_path:
        return on_path
    return next((p for p in SOFFICE_CANDIDATES if Path(p).exists()), None)

PROMPT = (
    "Transcribe this document into clean, faithful study-note markdown.\n"
    "- Use headings that mirror the slides/sections in order.\n"
    "- Render all math as LaTeX ($inline$ and $$block$$).\n"
    "- When a diagram, plot, or figure carries meaning, describe it in words.\n"
    "- Preserve technical detail; do NOT summarize it away and do NOT invent content.\n"
    "If the file is genuinely unreadable, output exactly: UNREADABLE"
)

CACHE = Path("cache")
RAW, PDF, MD = CACHE / "raw", CACHE / "pdf", CACHE / "md"
NOTES = Path("notes")


MANIFEST = CACHE / "files.json"      # canvas file identity -> content hash


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def recipe() -> str:
    """Fingerprint of the transcription inputs other than the file itself.

    Without this, editing PROMPT or switching GEMINI_MODEL silently reuses every
    old transcription — the same trap extract.pass1 already keys around.

    Deliberately does NOT cover extract_text(): text files are re-extracted on
    every run (see ingest_bytes), so changing that function needs no cache bust,
    and folding it in here would invalidate every paid vision transcription."""
    return hashlib.sha256((PROMPT + MODEL).encode()).hexdigest()[:12]


# Students paste screenshots into notebook markdown cells, which embeds the whole
# PNG as a base64 data URI. One notebook came to 847 KB and produced 687 chunks,
# 51% of the entire course index, of image bytes sliced at arbitrary offsets.
# Bounded by the closing paren / a non-base64 character, so this can't run on
# into real prose the way a greedy payload match would.
_DATA_URI_IMG = re.compile(r"!\[[^\]]*\]\(\s*data:[^)]*\)")
_DATA_URI_RAW = re.compile(r"data:[\w.+-]+/[\w.+-]+;base64,[A-Za-z0-9+/=]{100,}")


def strip_data_uris(text: str) -> str:
    """Replace embedded base64 payloads with a short placeholder.

    The alt text and surrounding prose are the searchable part of an image; the
    bytes are noise that crowds out real content and costs embedding time."""
    return _DATA_URI_RAW.sub("(embedded image)", _DATA_URI_IMG.sub("(embedded image)", text))


def _migrate_legacy_cache() -> int:
    """Adopt pre-recipe cache entries (`<sha256>.md`) into `<recipe>-<sha256>.md`.

    The transcription cache used to be keyed on file bytes alone. Adding the
    prompt+model recipe was the right fix, but it stranded every existing entry:
    without this, upgrading silently re-transcribes the whole corpus through a
    paid/rate-limited vision model. Entries predating the recipe were produced by
    the current PROMPT and MODEL, so adopting them is correct.

    Idempotent and cheap (a directory scan); safe to call on every run.
    """
    if not MD.exists():
        return 0
    r, moved = recipe(), 0
    for p in MD.glob("*.md"):
        if not re.fullmatch(r"[0-9a-f]{64}", p.stem):
            continue                                  # already namespaced
        target = MD / f"{r}-{p.stem}.md"
        if target.exists():
            p.unlink()                                # duplicate; keep the new one
        else:
            p.rename(target)
            moved += 1
    return moved


def _load_manifest() -> dict:
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}


def _save_manifest(m: dict):
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=0, sort_keys=True))


def _file_key(f) -> str:
    """Canvas-side identity of a file. If this is unchanged the bytes are
    unchanged, so we can skip the download entirely (slide decks are 9-17MB)."""
    return f"{f.id}:{getattr(f, 'updated_at', '')}:{getattr(f, 'size', '')}"


def to_pdf(src: Path, out_dir: Path) -> Path:
    """Convert pptx/docx -> pdf via LibreOffice headless."""
    if src.suffix.lower() == ".pdf":
        return src
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError(
            f"LibreOffice not found — needed to convert {src.name}. Install it "
            "(macOS: brew install --cask libreoffice; Debian/Ubuntu: apt install "
            "libreoffice) or set SOFFICE=/path/to/soffice.")
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([soffice, "--headless", "--convert-to", "pdf",
                    "--outdir", str(out_dir), str(src)],
                   check=True, capture_output=True, timeout=180)
    pdf = out_dir / (src.stem + ".pdf")
    if not pdf.exists():
        raise RuntimeError(f"conversion produced no pdf for {src.name}")
    return pdf


def gemini_markdown(client, pdf_path: Path) -> str:
    """Upload a pdf and get study-note markdown, with backoff on 429/503."""
    up = client.files.upload(file=str(pdf_path))
    for attempt in range(6):
        try:
            r = client.models.generate_content(model=MODEL, contents=[up, PROMPT])
            return r.text or ""
        except Exception as e:
            msg = str(e)
            transient = any(c in msg for c in ("503", "429", "500", "UNAVAILABLE", "RESOURCE_EXHAUSTED"))
            if not transient or attempt == 5:
                raise
            wait = min(60, 4 * 2 ** attempt)
            print(f"      transient ({msg[:40]}...) retry in {wait}s")
            time.sleep(wait)


def extract_text(path: Path) -> str:
    """Notebook / plain-text -> readable text (code cells fenced)."""
    if path.suffix.lower() == ".ipynb":
        nb = json.loads(path.read_text(errors="ignore"))
        parts = []
        for cell in nb.get("cells", []):
            src = cell.get("source", "")
            src = ("".join(src) if isinstance(src, list) else src).strip()
            if not src:
                continue
            parts.append(f"```python\n{src}\n```" if cell.get("cell_type") == "code" else src)
        return strip_data_uris("\n\n".join(parts))
    return strip_data_uris(path.read_text(errors="ignore"))


def _sectioned(title: str, text: str, max_chars: int = 1200) -> str:
    """Wrap raw text into size-bounded '## ' sections so chat.chunks_from_note can
    chunk it. Caps by chars (not lines) and hard-wraps giant lines, so a single
    huge line (e.g. a data literal in a notebook) can't create a massive chunk."""
    lines = []
    for line in text.splitlines():
        while len(line) > max_chars:
            lines.append(line[:max_chars]); line = line[max_chars:]
        lines.append(line)
    body, buf, size, n = [f"# {title}", ""], [], 0, 1
    def flush():
        nonlocal buf, size, n
        if buf:
            body.extend([f"## {title} — part {n}", *buf, ""]); n += 1; buf, size = [], 0
    for line in lines:
        if size + len(line) > max_chars:
            flush()
        buf.append(line); size += len(line) + 1
    flush()
    if n == 1:
        body += [f"## {title}", ""]
    return "\n".join(body)


def ingest_bytes(name, raw: Path, slug, client, out_name=None) -> tuple[str, str]:
    """Process one downloaded file -> notes/<slug>/<out>.md.
    Returns (status, content_hash) where status is new|cached|fail."""
    ext = raw.suffix.lower()
    h = sha256(raw.read_bytes())
    md_cache = MD / f"{recipe()}-{h}.md"
    out = NOTES / slug / ((out_name or Path(name).stem) + ".md")
    if ext not in TEXT_EXT and md_cache.exists():   # text extraction is free — always re-chunk
        out.write_text(md_cache.read_text())
        return "cached", h
    header = f"---\nsource: {name}\ncourse: {slug}\nsha256: {h}\n---\n\n"
    try:
        if ext in TEXT_EXT:
            body = _sectioned(Path(name).stem, extract_text(raw))
        elif ext in IMAGE_EXT:
            body = gemini_markdown(client, raw)      # no conversion — Gemini reads images
        else:
            pdf = to_pdf(raw, PDF) if ext != ".pdf" else raw
            if ext != ".pdf":
                pdf = pdf.rename(PDF / f"{h}.pdf")
            body = gemini_markdown(client, pdf)
        # Applies to every model-transcribed branch, images included. Skipping this
        # for images cached the failure as a success: the note's body became the
        # literal string "UNREADABLE" and it was never retried.
        if ext not in TEXT_EXT and (not body.strip() or body.strip() == "UNREADABLE"):
            return "fail", h
        md_cache.write_text(header + body)
        out.write_text(header + body)
        return "new", h
    except Exception as e:
        print(f"          FAILED {name}: {type(e).__name__} {str(e)[:70]}")
        return "fail", h


def ingest_assignments(course, slug, client, counts):
    """Homework: assignment descriptions -> assignments.md, and the prompt PDFs
    linked in each description -> ingested via the vision path (prefixed 'hw-')."""
    print("homework (assignment prompts):")
    desc_md, seen = [f"# {slug} Assignments", ""], set()
    try:
        assignments = list(course.get_assignments())
    except Exception as e:
        print(f"  assignments unavailable ({type(e).__name__}) — skipped")
        return
    for a in assignments:
        desc = getattr(a, "description", "") or ""
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", desc)).split())
        if text:
            desc_md += [f"## {a.name}", text, ""]
        for fid in dict.fromkeys(re.findall(r"/files/(\d+)", desc)):
            if fid in seen:
                continue
            seen.add(fid)
            try:
                f = course.get_file(int(fid))
            except Exception:
                continue
            if Path(f.display_name).suffix.lower() not in INGEST_EXT:
                continue                      # skip .zip/.xlsx/etc linked in prompts
            raw = RAW / slug / f.display_name
            f.download(str(raw))
            st, _h = ingest_bytes(f.display_name, raw, slug, client,
                                  out_name="hw-" + Path(f.display_name).stem)
            counts[st] += 1
            print(f"  {st:6} (prompt) {f.display_name}")
    for base in (NOTES / slug, Path("vault") / slug / "updates"):
        base.mkdir(parents=True, exist_ok=True)
        (base / "assignments.md").write_text("\n".join(desc_md))
    print("  wrote assignments.md")


def ingest_course(course_id: int, limit=None):
    load_dotenv(ROOT / ".env")
    canvas = get_client()
    from .canvas import gemini_key
    client = genai.Client(api_key=gemini_key())

    course = canvas.get_course(course_id)
    slug = course_label(course).split()[0]
    for d in (RAW / slug, PDF, MD, NOTES / slug):
        d.mkdir(parents=True, exist_ok=True)

    try:
        files = [f for f in course.get_files()
                 if Path(f.display_name).suffix.lower() in INGEST_EXT]
    except Exception as e:
        # Files tab is often disabled by the instructor — keep going, the
        # assignment prompts below are usually still readable.
        print(f"{slug}: files unavailable ({type(e).__name__}) — assignments only")
        files = []
    if limit:
        files = files[:limit]
    print(f"{slug}: {len(files)} ingestible file(s)")

    counts = {"new": 0, "cached": 0, "fail": 0, "new_files": []}
    adopted = _migrate_legacy_cache()
    if adopted:
        print(f"  adopted {adopted} cached transcription(s) from the pre-recipe cache")
    manifest = _load_manifest()
    for f in files:
        ext = Path(f.display_name).suffix.lower()
        out_name = "code-" + Path(f.display_name).stem if ext in TEXT_EXT else None
        out = NOTES / slug / ((out_name or Path(f.display_name).stem) + ".md")

        # Unchanged on Canvas + already transcribed -> skip the download entirely.
        # (Text files still re-download: they're small and re-chunking is free.)
        key = _file_key(f)
        known = manifest.get(key)
        if ext not in TEXT_EXT and known and (MD / f"{recipe()}-{known}.md").exists():
            out.write_text((MD / f"{recipe()}-{known}.md").read_text())
            counts["cached"] += 1
            print(f"  cached {f.display_name} (no download)")
            continue

        raw = RAW / slug / f.display_name
        f.download(str(raw))
        st, h = ingest_bytes(f.display_name, raw, slug, client, out_name)
        if st != "fail":
            manifest[key] = h
        if st == "new":
            counts["new_files"].append(f.display_name)
        counts[st] += 1
        print(f"  {st:6} {f.display_name}")
    _save_manifest(manifest)

    if not limit:
        ingest_assignments(course, slug, client, counts)

    print(f"\nsummary: {counts['new']} new, {counts['cached']} cached, {counts['fail']} failed")
    return counts


def main():
    chdir_root()      # data paths are relative to the repo root
    p = argparse.ArgumentParser(description="Ingest a course's files + homework")
    p.add_argument("course_id", type=int)
    p.add_argument("--limit", type=int, default=None, help="first N files only; skips homework")
    a = p.parse_args()
    ingest_course(a.course_id, a.limit)


if __name__ == "__main__":
    main()
