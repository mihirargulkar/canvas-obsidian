"""Regression tests for bugs found in the QA audit.

Each of these shipped once. They're grouped here so the failure modes stay
described in one place rather than scattered across the suite.
"""
import pathlib

from canvas_vault import canvas, changes, chat, extract


# --- slug: one definition, filesystem-safe -------------------------------------

def test_slug_of_matches_course_slug():
    """canvas.slug_of and Course.slug disagreed ("DS" vs "DS4400"), so the
    dashboard linked [[DS/Dashboard]] at a file living in vault/DS4400/."""
    from canvas_vault.course import Course

    class Fake:
        id = 1
        name = "DS 4400 Machine Learning"
        course_code = "DS4400.50397.202650"

    assert canvas.slug_of(Fake()) == Course.from_canvas(Fake()).slug == "DS4400"


def test_course_label_survives_null_name():
    class Fake:
        id = 5
        name = None

    assert canvas.course_label(Fake()) == "course 5"
    assert canvas.slug_of(Fake()) == "course-5"


# --- chunking: nothing silently unindexed ------------------------------------

def test_preamble_before_first_heading_is_indexed():
    """A note using only '#' headings produced ZERO chunks and was invisible to
    search — the transcription prompt invites exactly that shape."""
    note = "---\nsource: x.pptx\n---\n\n# Bias and Variance\n\n" + "Real content. " * 5
    chunks = chat.chunks_from_note(note, "Bias-variance", "DS4400")
    assert chunks, "note with no '## ' heading must still yield a chunk"
    assert "Real content" in chunks[0][1]
    assert "source: x.pptx" not in chunks[0][1], "frontmatter should be stripped"


# --- deadlines: past vs future ------------------------------------------------

def test_overdue_is_not_answered_with_a_future_window():
    """'what am I overdue on' routed to a forward-only window and answered
    'nothing due in the next 7 days' — the opposite of the truth."""
    assert chat.route("what assignments am I overdue on") == "deadline"
    assert hasattr(canvas, "overdue"), "needs a backward-looking query"


def test_day_count_requires_the_word_day():
    import re
    pat = r"(\d+)\s*days?\b"
    assert re.search(pat, "what's due in 10 days").group(1) == "10"
    assert re.search(pat, "the 2 week project") is None


# --- state: never persist a failed read --------------------------------------

def test_failed_read_does_not_wipe_seen_state(tmp_path, monkeypatch):
    """A 403/rate-limited fetch yields empty lists; saving them wiped the
    seen-set, so the next healthy sync re-reported the whole term as new."""
    monkeypatch.setattr(changes, "STATE", tmp_path / "seen.json")
    changes.diff_course("X", [{"date": "2026-07-01", "title": "Exam moved"}], ["HW1"])
    changes.diff_course("X", [], [], complete=False)          # simulated failure
    again = changes.diff_course("X", [{"date": "2026-07-01", "title": "Exam moved"}], ["HW1"])
    assert again["announcements"] == [], "already-seen items must not be re-reported"
    assert again["assignments"] == []


# --- vault writes: never delete before the replacement is written -------------

def test_write_failure_does_not_destroy_existing_notes(tmp_path, monkeypatch):
    """The stale purge ran BEFORE the writes meant to replace those notes, so any
    write error left the graph deleted and unwritten."""
    monkeypatch.chdir(tmp_path)
    concepts = tmp_path / "vault" / "T" / "concepts"
    concepts.mkdir(parents=True)
    for i in range(5):
        (concepts / f"Old{i}.md").write_text("---\nlectures: [L1]\n---\n\n# Old\n\nx\n")

    real, calls = pathlib.Path.write_text, {"n": 0}

    def flaky(self, *a, **k):
        if "concepts" in str(self):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError(28, "No space left on device")
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "write_text", flaky)
    nodes = {"L1": [{"name": f"New{i}", "definition": "d", "related": []} for i in range(4)]}
    try:
        extract.pass2("T", nodes, complete=True)
    except OSError:
        pass
    assert len(list(concepts.glob("Old*.md"))) == 5, "originals must survive a failed write"


def test_safe_filename_is_length_capped():
    """An over-long concept name raised ENAMETOOLONG mid-write."""
    assert len(extract.safe_filename("Z" * 400)) <= 120


# --- cache migration: a key change must not strand existing work --------------

def test_legacy_transcription_cache_is_adopted(tmp_path, monkeypatch):
    """Adding prompt+model to the cache key stranded every pre-existing entry,
    which would silently re-transcribe a whole corpus through a rate-limited
    vision model on upgrade."""
    from canvas_vault import ingest

    monkeypatch.chdir(tmp_path)
    md = tmp_path / "cache" / "md"
    md.mkdir(parents=True)
    monkeypatch.setattr(ingest, "MD", md)

    old, dup = "a" * 64, "b" * 64
    (md / f"{old}.md").write_text("legacy transcription")
    (md / f"{dup}.md").write_text("stale duplicate")
    (md / f"{ingest.recipe()}-{dup}.md").write_text("current")
    (md / "unrelated.md").write_text("not a cache entry")

    assert ingest._migrate_legacy_cache() == 1
    assert (md / f"{ingest.recipe()}-{old}.md").read_text() == "legacy transcription"
    assert (md / f"{ingest.recipe()}-{dup}.md").read_text() == "current"
    assert not [p for p in md.glob("*.md") if len(p.stem) == 64]
    assert (md / "unrelated.md").exists()
    assert ingest._migrate_legacy_cache() == 0, "must be idempotent"
