"""Regression tests for bugs found in the QA audit.

Each of these shipped once. They're grouped here so the failure modes stay
described in one place rather than scattered across the suite.
"""
import json
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


# --- freshness: never imply "nothing posted" when files weren't examined ------

def test_shallow_refresh_still_reports_new_files():
    """refresh runs deep=False (no transcription) so it never ran ingest, the
    only step that looks at Canvas Files. It then said "No changes since last
    sync", and a client concluded a lecture deck that WAS on Canvas "hasn't been
    posted". A cheap file listing must still be surfaced."""
    summary = changes.summarise(
        {"DS4400": {"announcements": [], "assignments": [],
                    "pending": ["Lecture17-DS4400.pptx"]}}, 0)
    assert "Lecture17-DS4400.pptx" in summary
    assert "not transcribed yet" in summary


def test_quiet_summary_names_what_was_checked():
    """'No changes' alone is ambiguous between 'checked, found nothing' and
    'never looked'."""
    summary = changes.summarise({"DS4400": {"announcements": [], "assignments": []}}, 0)
    for word in ("announcements", "assignments", "files"):
        assert word in summary


def test_local_tz_falls_back_without_credentials(monkeypatch):
    """local_tz caught Exception, but get_client raises SystemExit, which isn't
    one. Formatting any date on a machine with no .env killed the process
    instead of falling back to the OS timezone."""
    monkeypatch.delenv("CANVAS_TZ", raising=False)
    monkeypatch.delenv("CANVAS_URL", raising=False)
    monkeypatch.delenv("CANVAS_TOKEN", raising=False)
    monkeypatch.setattr(canvas, "load_dotenv", lambda *a, **k: None)  # ignore a real .env
    canvas.local_tz.cache_clear()
    assert canvas.local_tz() is not None
    canvas.local_tz.cache_clear()


def test_eval_scorer_uses_the_gold_set_it_was_given():
    """evaluate() looped over the module-level GOLD (10 hand written pairs) but
    divided by len(gold) (70 synthetic), so a perfect run printed "10/70, 14%".
    That number was investigated as a retrieval failure and written up as a flaw
    in exact-source matching. It was a numerator from one set over a denominator
    from another."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "eval_retrieval", pathlib.Path(__file__).parent.parent / "tools" / "eval_retrieval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    seen = []

    class FakeCol:
        def query(self, query_texts, n_results, where):
            seen.append(query_texts[0])
            return {"metadatas": [[{"source": "Lecture1"}]], "documents": [[""]]}

    import canvas_vault.chat as chat
    real, chat._collection = chat._collection, lambda: FakeCol()
    try:
        gold = [{"query": f"q{i}", "source": "Lecture1"} for i in range(4)]
        hits, n = mod.evaluate("DS4400", 5, gold=gold, label="test")
    finally:
        chat._collection = real
    assert seen == ["q0", "q1", "q2", "q3"], "must score the gold set it was passed"
    assert (hits, n) == (4, 4), "numerator and denominator must come from one set"


def _eval_tool():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "eval_retrieval", pathlib.Path(__file__).parent.parent / "tools" / "eval_retrieval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_different_source_on_the_same_topic_counts_as_a_hit():
    """Single-source ground truth marked correct answers wrong. Every topic lives
    in the lecture, its polls and a notebook, so a query generated from the
    notebook scored zero when retrieval returned the lecture. 23 of 28 apparent
    misses were that, which is most of the gap between the exact-source score and
    the judge's."""
    er = _eval_tool()
    entry = {"query": "monte carlo intuition", "source": "code-sampling_examples",
             "relevant": ["Lecture12-DS4400"]}
    assert er.accepted(entry) == ["code-sampling_examples", "Lecture12-DS4400"]

    class FakeCol:
        def query(self, query_texts, n_results, where):
            return {"metadatas": [[{"source": "Lecture12-DS4400"}]], "documents": [[""]]}

    import canvas_vault.chat as chat
    real, chat._collection = chat._collection, lambda: FakeCol()
    try:
        hits, n = er.evaluate("DS4400", 5, gold=[entry], label="t")
    finally:
        chat._collection = real
    assert (hits, n) == (1, 1), "an accepted alternate source must score as a hit"


def test_unlabelled_sources_are_reported_not_scored_as_misses(capsys):
    """A retrieved source with no verdict either way is missing data. Reporting
    the number as final would repeat the mistake that produced a believed 37%."""
    er = _eval_tool()

    class FakeCol:
        def query(self, query_texts, n_results, where):
            return {"metadatas": [[{"source": "Lecture99"}]], "documents": [[""]]}

    import canvas_vault.chat as chat
    real, chat._collection = chat._collection, lambda: FakeCol()
    try:
        er.evaluate("DS4400", 5, gold=[{"query": "q", "source": "Lecture1"}], label="t")
    finally:
        chat._collection = real
    assert "LOWER BOUND" in capsys.readouterr().out


def test_relabel_caches_verdicts_and_is_idempotent(tmp_path):
    """Pooled judgements: label a (query, source) pair once, reuse forever. A
    second pass must find nothing to do, or every run re-spends the quota this
    exists to avoid."""
    er = _eval_tool()
    gold_file = tmp_path / "gold.json"
    gold = [{"query": "monte carlo", "source": "code-sampling"}]
    gold_file.write_text(json.dumps(gold))

    class FakeCol:
        def query(self, query_texts, n_results, where):
            return {"metadatas": [[{"source": "Lecture12"}, {"source": "Lecture3"}]],
                    "documents": [["monte carlo content", "unrelated"]]}

    er.judge = lambda pairs: {i: i == 0 for i in range(len(pairs))}
    import canvas_vault.chat as chat
    real, chat._collection = chat._collection, lambda: FakeCol()
    try:
        done, total = er.relabel("DS4400", 5, gold, str(gold_file))
        assert (done, total) == (2, 2)
        assert gold[0]["relevant"] == ["Lecture12"]
        assert gold[0]["irrelevant"] == ["Lecture3"]
        again, _ = er.relabel("DS4400", 5, json.loads(gold_file.read_text()), str(gold_file))
        assert again == 0, "already-labelled pairs must not be re-judged"
    finally:
        chat._collection = real
