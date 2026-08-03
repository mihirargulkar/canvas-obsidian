"""End-to-end pipeline tests against synthetic courses.

These cover the modules that previously had no tests at all (store, updates,
dashboard, sync paths) because they used to need a live Canvas account and a
vision model. The fixtures in conftest.py replace both.
"""
import numpy as np
import pytest

from canvas_vault import canvas, chat, dashboard, extract, updates
from canvas_vault.course import Course
from canvas_vault.store import VectorStore, bm25_scores, rrf


# --- store --------------------------------------------------------------------

def test_store_roundtrip_and_search(tmp_path, stub_embed):
    s = VectorStore(tmp_path / "v.db", stub_embed, embedder_id="stub")
    s.upsert(["a", "b", "c"],
             ["newton second law force equals mass times acceleration",
              "the estates general assembly of seventeen eighty nine",
              "momentum is conserved in a closed system"],
             [{"course": "PHYS1100"}, {"course": "HIST2200"}, {"course": "PHYS1100"}])
    assert s.count() == 3

    hit = s.query(["force equals mass times acceleration"], n_results=1)
    assert "newton" in hit["documents"][0][0]

    only_hist = s.query(["assembly"], n_results=5, where={"course": "HIST2200"})
    assert all(m["course"] == "HIST2200" for m in only_hist["metadatas"][0])


def test_store_upsert_is_idempotent(tmp_path, stub_embed):
    s = VectorStore(tmp_path / "v.db", stub_embed, embedder_id="stub")
    s.upsert(["a"], ["first version of the text"], [{"course": "X"}])
    s.upsert(["a"], ["second version of the text"], [{"course": "X"}])
    assert s.count() == 1, "same id must update, not duplicate"
    assert "second" in s.get()["documents"][0]


def test_store_delete(tmp_path, stub_embed):
    s = VectorStore(tmp_path / "v.db", stub_embed, embedder_id="stub")
    s.upsert(["a", "b"], ["text one here", "text two here"], [{}, {}])
    s.delete(["a"])
    assert s.get()["ids"] == ["b"]


def test_store_empty_query_is_not_a_crash(tmp_path, stub_embed):
    s = VectorStore(tmp_path / "v.db", stub_embed, embedder_id="stub")
    out = s.query(["anything"], n_results=3)
    assert out == {"documents": [[]], "metadatas": [[]]}


def test_bm25_prefers_exact_term_matches():
    docs = ["gradient descent updates the weights",
            "the estates general met in seventeen eighty nine",
            "momentum is conserved"]
    assert int(np.argmax(bm25_scores("estates general", docs))) == 1


def test_rrf_puts_agreed_top_hit_first():
    assert rrf([2, 0, 1], [2, 1, 0])[0] == 2, "top of both rankings must win"


def test_rrf_beats_a_single_ranking_top_hit():
    """The property that makes fusion useful: something both retrievers like
    outranks something only one of them found. Here 1 is second in the dense
    list but present in both, while 0 is first in one and absent from the other.
    """
    assert rrf([0, 1], [1])[0] == 1


# --- indexing and search over synthetic notes ---------------------------------

def test_index_and_search_across_two_courses(synthetic_notes, stub_embed, monkeypatch):
    monkeypatch.setattr(chat, "_embedder", lambda: (stub_embed, "stub"))
    monkeypatch.setattr(chat, "_STORE", None)

    changed = chat.index(quiet=True)
    assert changed > 0

    hits = chat._collection().query(["estates general assembly"], n_results=3)
    assert any("Estates" in d for d in hits["documents"][0])

    scoped = chat._collection().query(["lecture"], n_results=5, where={"course": "PHYS1100"})
    assert all(m["course"] == "PHYS1100" for m in scoped["metadatas"][0])


def test_reindex_is_incremental(synthetic_notes, stub_embed, monkeypatch):
    monkeypatch.setattr(chat, "_embedder", lambda: (stub_embed, "stub"))
    monkeypatch.setattr(chat, "_STORE", None)
    chat.index(quiet=True)
    assert chat.index(quiet=True) == 0, "a second run with no edits must change nothing"

    (synthetic_notes / "notes" / "PHYS1100" / "Lecture2.md").write_text(
        "---\nsource: L2.pptx\n---\n\n## Energy\n\nWork is force times distance, and "
        "kinetic energy follows from integrating it.\n")
    assert chat.index(quiet=True) > 0, "a new note must be picked up"


def test_homework_is_searchable_but_not_a_lecture(synthetic_notes):
    from pathlib import Path
    notes = Path("notes/PHYS1100")
    assert extract.is_lecture(notes / "Lecture1.md")
    assert not extract.is_lecture(notes / "hw-ProblemSet1.md"), \
        "homework must stay out of the concept graph"


# --- concept graph, without calling a model -----------------------------------

def test_graph_build_and_read(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes" / "PHYS1100").mkdir(parents=True)
    concepts = {"Lecture1": [
        {"name": "Newton's Second Law", "definition": "F = ma.",
         "related": ["Inertia", "Momentum"]},
        {"name": "Inertia", "definition": "Resistance to change in motion.", "related": []},
        {"name": "Momentum", "definition": "Mass times velocity.",
         "related": ["Newton's Second Law"]},
    ]}
    extract.pass2("PHYS1100", concepts, complete=True)

    g = extract.graph_data("PHYS1100")
    assert len(g["nodes"]) == 3
    assert g["edges"], "related concepts should produce edges"

    one = extract.concept_data("PHYS1100", "Momentum")
    assert one["definition"].startswith("Mass times")
    assert "Newton's Second Law" in one["links"]
    assert extract.concept_data("PHYS1100", "Nonexistent") is None


# --- deadlines against fake Canvas --------------------------------------------

def test_upcoming_skips_undated_and_far_future(phys_course):
    from datetime import datetime, timedelta, timezone
    soon = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    phys_course._assignments[0].due_at = soon
    rows = canvas.upcoming(7, courses=[phys_course])
    assert [r[2] for r in rows] == ["Problem Set 1"]
    assert rows[0][1] == "PHYS1100"


def test_one_restricted_course_does_not_sink_the_others(phys_course, hist_course):
    """A 403 on one course used to raise and kill deadlines for every class."""
    phys_course._forbid.add("assignments")
    rows = canvas.upcoming(3650, courses=[phys_course, hist_course])
    assert [r[1] for r in rows] == ["HIST2200"], "the readable course must still report"


# --- updates and dashboards ---------------------------------------------------

def test_updates_strip_html_and_write_both_places(tmp_path, monkeypatch, hist_course):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(updates, "fetch_updates",
                        lambda cid: {"syllabus": "Late work loses 10% per day.",
                                     "announcements": [{"date": "2026-09-01",
                                                        "title": "Reading list posted",
                                                        "body": "See the syllabus."}]})
    data = updates.fetch_updates(hist_course.id)
    updates.write_notes("HIST2200", data)
    ann = (tmp_path / "notes" / "HIST2200" / "announcements.md").read_text()
    assert "Reading list posted" in ann
    assert (tmp_path / "vault" / "HIST2200" / "updates" / "syllabus.md").exists()


def test_strip_html_removes_markup():
    out = updates.strip_html("<p>Lab is <b>Friday</b></p><script>x=1</script>")
    assert "Friday" in out and "<b>" not in out and "x=1" not in out


def test_course_dashboard_links_its_own_slug(tmp_path, monkeypatch, hist_course):
    monkeypatch.chdir(tmp_path)
    c = Course.from_canvas(hist_course)
    monkeypatch.setattr(Course, "upcoming", lambda self, days=14: [])
    monkeypatch.setattr(updates, "fetch_updates",
                        lambda cid: {"syllabus": "", "announcements": []})
    dashboard.course_dashboard(c, 14)
    md = (tmp_path / "vault" / "HIST2200" / "Dashboard.md").read_text()
    assert "HIST2200" in md


# --- Course identity ----------------------------------------------------------

def test_course_from_canvas_uses_course_code(phys_course, hist_course):
    assert Course.from_canvas(phys_course).slug == "PHYS1100"
    assert Course.from_canvas(hist_course).slug == "HIST2200"


def test_pending_files_ignores_non_ingestible(tmp_path, monkeypatch, hist_course):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes" / "HIST2200").mkdir(parents=True)
    c = Course.from_canvas(hist_course)
    monkeypatch.setattr(type(c), "_api", property(lambda self: hist_course))
    pending = c.pending_files()
    assert "Lecture1-Revolutions.pptx" in pending
    assert "readings.csv" not in pending, ".csv is never transcribed, so it isn't missing"


def test_pending_files_empty_when_files_restricted(tmp_path, monkeypatch, phys_course):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes" / "PHYS1100").mkdir(parents=True)
    c = Course.from_canvas(phys_course)
    monkeypatch.setattr(type(c), "_api", property(lambda self: phys_course))
    assert c.pending_files() == []
