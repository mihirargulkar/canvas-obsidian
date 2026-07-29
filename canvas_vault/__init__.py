"""Canvas -> Obsidian vault: sync coursework, build a concept graph, serve it to an LLM.

Data (notes/, vault/, cache/, chroma_db/) lives beside the package at ROOT, and the
modules address it with relative paths. Entry points call `chdir_root()` so the tool
works regardless of where it was launched from: an MCP client starts the server from
an arbitrary directory, and a scheduled job may not set one at all.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

__all__ = ["ROOT", "chdir_root"]


def chdir_root():
    """Anchor the process to the project root. Call from entry points only."""
    os.chdir(ROOT)
