"""Canvas -> Obsidian vault: sync coursework, build a concept graph, serve it to an LLM.

Data (notes/, vault/, cache/, chroma_db/) lives beside the package at ROOT, and the
modules address it with relative paths. Entry points call `chdir_root()` so the tool
works regardless of where it was launched from: an MCP client starts the server from
an arbitrary directory, and a scheduled job may not set one at all.
"""
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

__all__ = ["ROOT", "chdir_root", "replace_file", "remove_file"]

# Windows refuses to delete or rename a file another process holds open, and
# something usually does: an antivirus scanner opening every new file, Obsidian
# watching the vault, LibreOffice not yet closing the PDF it just wrote. The
# handle is normally released within a second, so a short retry turns a hard
# failure into a pause. POSIX has no such rule and these loops never spin there.
_RETRIES = 5
_BACKOFF = 0.1


def _with_retry(op, path):
    last = None
    for attempt in range(_RETRIES):
        try:
            return op()
        except PermissionError as e:          # WinError 32: file in use
            last = e
            time.sleep(_BACKOFF * 2 ** attempt)
        except FileNotFoundError:
            return None                        # already gone; nothing to do
    raise last


def replace_file(src, dst):
    """Move src onto dst, overwriting. os.replace, never Path.rename.

    Path.rename raises FileExistsError on Windows when the target exists, where
    POSIX silently overwrites — so code written and tested on a Mac works until
    the first re-run on Windows. os.replace overwrites atomically on both.
    """
    src, dst = Path(src), Path(dst)
    _with_retry(lambda: os.replace(src, dst), src)
    return dst


def remove_file(path) -> bool:
    """Delete a file if it exists. Returns False if it could not be removed.

    Does not raise on a locked file: callers here are purging stale data, and a
    leftover file is a far smaller problem than aborting the run that would have
    replaced it.
    """
    try:
        _with_retry(lambda: Path(path).unlink(missing_ok=True), path)
        return True
    except OSError:
        return False


def chdir_root():
    """Anchor the process to the project root. Call from entry points only."""
    os.chdir(ROOT)
