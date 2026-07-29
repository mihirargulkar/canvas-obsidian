#!/usr/bin/env python3
"""Phase 2: ingest a course's files + homework into faithful markdown notes.

    python ingest.py 253025            # files (slides, notebooks, docs) + homework
    python ingest.py 253025 --limit 3  # first 3 files (cheap trial)

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
import sys
import time
from pathlib import Path

from google import genai
from dotenv import load_dotenv

from canvas import get_client, course_label

VISION_EXT = {".pdf", ".pptx", ".docx"}      # -> pdf -> Gemini vision
TEXT_EXT = {".ipynb", ".txt", ".md"}         # -> extracted directly, no model
INGEST_EXT = VISION_EXT | TEXT_EXT
MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"

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


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def to_pdf(src: Path, out_dir: Path) -> Path:
    """Convert pptx/docx -> pdf via LibreOffice headless."""
    if src.suffix.lower() == ".pdf":
        return src
    if not Path(SOFFICE).exists():
        sys.exit(f"LibreOffice not found at {SOFFICE} — needed to convert {src.name}")
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([SOFFICE, "--headless", "--convert-to", "pdf",
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
        return "\n\n".join(parts)
    return path.read_text(errors="ignore")


def _sectioned(title: str, text: str, max_lines: int = 45) -> str:
    """Wrap raw text into '## ' sections so chat.chunks_from_note can chunk it."""
    lines = text.splitlines() or [""]
    body = [f"# {title}", ""]
    for n, i in enumerate(range(0, len(lines), max_lines), 1):
        body.append(f"## {title} — part {n}")
        body += lines[i:i + max_lines]
        body.append("")
    return "\n".join(body)


def ingest_bytes(name, raw: Path, slug, client, out_name=None) -> str:
    """Process one downloaded file -> notes/<slug>/<out>.md. Returns new|cached|fail."""
    ext = raw.suffix.lower()
    h = sha256(raw.read_bytes())
    md_cache = MD / f"{h}.md"
    out = NOTES / slug / ((out_name or Path(name).stem) + ".md")
    if md_cache.exists():
        out.write_text(md_cache.read_text())
        return "cached"
    header = f"---\nsource: {name}\ncourse: {slug}\nsha256: {h}\n---\n\n"
    try:
        if ext in TEXT_EXT:
            body = _sectioned(Path(name).stem, extract_text(raw))
        else:
            pdf = to_pdf(raw, PDF) if ext != ".pdf" else raw
            if ext != ".pdf":
                pdf = pdf.rename(PDF / f"{h}.pdf")
            body = gemini_markdown(client, pdf)
            if body.strip() == "UNREADABLE" or not body.strip():
                return "fail"
        md_cache.write_text(header + body)
        out.write_text(header + body)
        return "new"
    except Exception as e:
        print(f"          FAILED {name}: {type(e).__name__} {str(e)[:70]}")
        return "fail"


def ingest_assignments(course, slug, client, counts):
    """Homework: assignment descriptions -> assignments.md, and the prompt PDFs
    linked in each description -> ingested via the vision path (prefixed 'hw-')."""
    print("homework (assignment prompts):")
    desc_md, seen = [f"# {slug} Assignments", ""], set()
    for a in course.get_assignments():
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
            raw = RAW / slug / f.display_name
            f.download(str(raw))
            st = ingest_bytes(f.display_name, raw, slug, client,
                              out_name="hw-" + Path(f.display_name).stem)
            counts[st] += 1
            print(f"  {st:6} (prompt) {f.display_name}")
    for base in (NOTES / slug, Path("vault") / "updates"):
        base.mkdir(parents=True, exist_ok=True)
        (base / "assignments.md").write_text("\n".join(desc_md))
    print("  wrote assignments.md")


def ingest_course(course_id: int, limit=None):
    load_dotenv(str(Path(__file__).parent / ".env"))
    canvas = get_client()
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    course = canvas.get_course(course_id)
    slug = course_label(course).split()[0]
    for d in (RAW / slug, PDF, MD, NOTES / slug):
        d.mkdir(parents=True, exist_ok=True)

    files = [f for f in course.get_files()
             if Path(f.display_name).suffix.lower() in INGEST_EXT]
    if limit:
        files = files[:limit]
    print(f"{slug}: {len(files)} ingestible file(s)")

    counts = {"new": 0, "cached": 0, "fail": 0}
    for f in files:
        ext = Path(f.display_name).suffix.lower()
        raw = RAW / slug / f.display_name
        f.download(str(raw))
        out_name = "code-" + Path(f.display_name).stem if ext in TEXT_EXT else None
        st = ingest_bytes(f.display_name, raw, slug, client, out_name)
        counts[st] += 1
        print(f"  {st:6} {f.display_name}")

    if not limit:
        ingest_assignments(course, slug, client, counts)

    print(f"\nsummary: {counts['new']} new, {counts['cached']} cached, {counts['fail']} failed")
    return counts


def main():
    p = argparse.ArgumentParser(description="Ingest a course's files + homework")
    p.add_argument("course_id", type=int)
    p.add_argument("--limit", type=int, default=None, help="first N files only; skips homework")
    a = p.parse_args()
    ingest_course(a.course_id, a.limit)


if __name__ == "__main__":
    main()
