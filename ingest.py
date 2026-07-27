#!/usr/bin/env python3
"""Phase 2: ingest one course's files -> faithful markdown notes via Gemini vision.

    python ingest.py 253025            # ingest DS4400
    python ingest.py 253025 --limit 3  # first 3 ingestible files (cheap trial)

Content-hash cache: unchanged files are never re-sent to Gemini. Re-runs of an
already-ingested course cost 0 model calls. Failed files are retried next run.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from google import genai
from dotenv import load_dotenv

from canvas import get_client, course_label

INGEST_EXT = {".pdf", ".pptx"}          # ponytail: pilot scope; add .ipynb/.docx when a phase needs them
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
    """Convert pptx->pdf via LibreOffice headless. Returns the produced pdf path."""
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


def ingest_course(course_id: int, limit=None):
    load_dotenv(str(Path(__file__).parent / ".env"))
    canvas = get_client()
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    course = canvas.get_course(course_id)
    slug = course_label(course).split()[0]  # e.g. "DS4400"
    for d in (RAW / slug, PDF, MD, NOTES / slug):
        d.mkdir(parents=True, exist_ok=True)

    files = [f for f in course.get_files()
             if Path(f.display_name).suffix.lower() in INGEST_EXT]
    if limit:
        files = files[:limit]
    print(f"{slug}: {len(files)} ingestible file(s)\n")

    n_cached = n_new = n_fail = 0
    for f in files:
        name = f.display_name
        raw = RAW / slug / name
        f.download(str(raw))
        h = sha256(raw.read_bytes())
        md_cache = MD / f"{h}.md"
        out = NOTES / slug / (Path(name).stem + ".md")

        if md_cache.exists():
            out.write_text(md_cache.read_text())
            n_cached += 1
            print(f"  cached  {name}")
            continue

        try:
            print(f"  new     {name} -> pdf -> Gemini")
            pdf = to_pdf(raw, PDF) if raw.suffix.lower() == ".pptx" else raw
            if raw.suffix.lower() == ".pptx":  # keep converted pdf keyed by hash
                pdf = pdf.rename(PDF / f"{h}.pdf")
            md = gemini_markdown(client, pdf)
            if md.strip() == "UNREADABLE" or not md.strip():
                n_fail += 1
                print(f"          UNREADABLE — skipped (will retry next run)")
                continue
            header = f"---\nsource: {name}\ncourse: {slug}\nsha256: {h}\n---\n\n"
            md_cache.write_text(header + md)   # cache keyed by source content hash
            out.write_text(header + md)
            n_new += 1
        except Exception as e:
            n_fail += 1
            print(f"          FAILED: {type(e).__name__} {str(e)[:80]} (will retry next run)")

    print(f"\nsummary: {n_new} new, {n_cached} cached, {n_fail} failed")
    return n_new, n_cached, n_fail


def main():
    p = argparse.ArgumentParser(description="Ingest a course's files to markdown (Phase 2)")
    p.add_argument("course_id", type=int)
    p.add_argument("--limit", type=int, default=None)
    a = p.parse_args()
    ingest_course(a.course_id, a.limit)


if __name__ == "__main__":
    main()
