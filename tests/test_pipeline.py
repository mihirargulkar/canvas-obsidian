"""End-to-end pipeline tests against synthetic courses.

These cover the modules that previously had no tests at all (store, updates,
dashboard, sync paths) because they used to need a live Canvas account and a
vision model. The fixtures in conftest.py replace both.
"""
import pathlib

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


def test_restricted_files_tab_falls_back_to_modules(tmp_path, monkeypatch, phys_course):
    """A 403 on the Files tab does NOT mean the files are unreachable: anything
    published in a Module is still fetchable by id. A real course returned zero
    files this way while holding its lectures, syllabus and schedule in modules,
    so the tool ingested nothing and pending_files() said [] — "nothing pending"
    rather than "could not look"."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes" / "PHYS1100").mkdir(parents=True)
    c = Course.from_canvas(phys_course)
    monkeypatch.setattr(type(c), "_api", property(lambda self: phys_course))
    assert sorted(c.pending_files()) == ["Lecture1-Newton.pptx", "Syllabus.pdf"]


def test_course_files_dedupes_across_both_sources(hist_course):
    """A file listed in the Files tab AND in a module is one file."""
    from tests.conftest import FakeModule, FakeModuleItem
    hist_course._modules = [FakeModule("M", [FakeModuleItem("Lecture1-Revolutions.pptx", 9001)])]
    names = [f.display_name for f in canvas.course_files(hist_course)]
    assert names.count("Lecture1-Revolutions.pptx") == 1


def test_course_files_survives_both_sources_failing(phys_course):
    """Files 403 and modules disabled must yield [], not an exception."""
    phys_course._forbid |= {"modules"}
    assert canvas.course_files(phys_course) == []


def test_xlsx_is_extracted_without_a_dependency(tmp_path):
    """Course schedules live in .xlsx (exam dates, deadlines, weekly topics),
    which is exactly what a student searches for. An xlsx is a zip of XML, so
    stdlib is enough and openpyxl is not worth the install."""
    import zipfile
    from canvas_vault import ingest
    f = tmp_path / "sched.xlsx"
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("xl/sharedStrings.xml",
                   '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<si><t>Date</t></si><si><t>Topic</t></si>'
                   '<si><t>Oct. 16</t></si><si><t>Midterm Exam</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml",
                   '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<sheetData>'
                   '<row><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                   '<row><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c></row>'
                   '</sheetData></worksheet>')
    out = ingest.extract_text(f)
    assert "Date | Topic" in out
    assert "Oct. 16 | Midterm Exam" in out
    assert ".xlsx" in str(ingest.TEXT_EXT)


# --- cross-lecture linking ----------------------------------------------------

def test_subsumption_links_specific_concepts_to_general_ones():
    """Pass 1 sees one lecture at a time, so "Gradient Descent" -> "Gradient" only
    got made when both happened to appear in the same deck. Cross-lecture edges
    are the point of the graph, so a link the names already state should not
    depend on the model guessing another lecture's wording."""
    nodes = {n: {"related": set(), "lectures": {"L1"}} for n in
             ["gradient descent", "gradient", "l2 regularization", "regularization",
              "partial derivative", "derivative", "iterate", "rate"]}
    extract.add_subsumption_links(nodes)
    assert "gradient" in nodes["gradient descent"]["related"]
    assert "regularization" in nodes["l2 regularization"]["related"]
    assert "derivative" in nodes["partial derivative"]["related"]
    assert "rate" not in nodes["iterate"]["related"], "must match tokens, not substrings"
    assert not nodes["gradient"]["related"], "the general concept must not link back"


def test_aliases_merge_into_the_better_attested_name():
    """One deck used "basis function" and "feature map" for the same idea, so the
    concept split into two nodes each holding half its lectures and half its
    edges. The survivor is the name covering more lectures."""
    nodes = {
        "feature map": {"related": {"linear regression"}, "lectures": {"L1", "L2", "L4"},
                        "aka": {"basis function"}},
        "basis function": {"related": {"polynomial"}, "lectures": {"L1"}, "aka": set()},
        "linear regression": {"related": {"basis function"}, "lectures": {"L1"}, "aka": set()},
    }
    assert extract.merge_aliases(nodes) == 1
    assert "basis function" not in nodes, "the alias node must be folded away"
    assert nodes["feature map"]["lectures"] == {"L1", "L2", "L4"}
    assert "polynomial" in nodes["feature map"]["related"], "its edges must survive"
    assert nodes["linear regression"]["related"] == {"feature map"}, "edges repoint"


def test_alias_merge_keeps_the_name_with_more_lectures():
    """Otherwise the surviving name depends on dict ordering rather than on which
    term the course actually leans on."""
    nodes = {
        "feature map": {"related": set(), "lectures": {"L1"}, "aka": {"basis function"}},
        "basis function": {"related": set(), "lectures": {"L1", "L2", "L3"}, "aka": set()},
    }
    extract.merge_aliases(nodes)
    assert "basis function" in nodes, "the better-attested name must win"


def test_graph_eval_reports_a_random_baseline(tmp_path):
    """"Within 2 hops" is trivially won by adding edges, so the gold score is
    uninterpretable without a control. A fully connected graph must show ~no lift
    even though every gold pair "passes"."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "eval_graph", pathlib.Path(__file__).parent.parent / "tools" / "eval_graph.py")
    eg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eg)

    names = [f"c{i}" for i in range(12)]
    complete = {frozenset((a, b)) for a in names for b in names if a != b}
    assert eg.random_baseline(dict.fromkeys(names), complete, trials=50) == 1.0

    star = {frozenset(("c0", n)) for n in names[1:]}      # hub: all pairs are 2 hops
    assert eg.random_baseline(dict.fromkeys(names), star, trials=50) == 1.0

    sparse = {frozenset(("c0", "c1")), frozenset(("c2", "c3"))}
    assert eg.random_baseline(dict.fromkeys(names), sparse, trials=200) < 0.2


# --- grades -------------------------------------------------------------------

def test_grades_report_both_totals(hist_course):
    """current_score counts graded work only; final_score treats ungraded as zero.
    Early in a term these differ enormously (91.5 vs 64.0 here), so reporting one
    without saying which would badly mislead."""
    row = canvas.grades(courses=[hist_course])[0]
    assert row["course"] == "HIST2200"
    assert (row["current_score"], row["final_score"]) == (91.5, 64.0)
    assert row["letter"] == "A-"


def test_grades_list_marked_and_pending_work(hist_course):
    """"What haven't I got back yet" is a grade question too, so submitted-but-
    unmarked work is included with a null score. Never-attempted work is not,
    since it isn't awaiting anything."""
    items = canvas.grades(courses=[hist_course])[0]["items"]
    by_name = {i["name"]: i for i in items}
    assert by_name["Essay 1"]["score"] == 23 and by_name["Essay 1"]["out_of"] == 25
    assert by_name["Essay 2"]["score"] is None
    assert by_name["Essay 2"]["status"] == "submitted"
    assert "Extra credit" not in by_name, "unsubmitted and unmarked is not a grade row"
    assert [i["name"] for i in items][:2] == ["Essay 1", "Quiz 1"], "newest graded first"


def test_hidden_grades_report_an_error_not_a_zero(phys_course, hist_course):
    """An instructor hiding the total must not read as a zero, and must not take
    down every other class's grades."""
    rows = {r["course"]: r for r in canvas.grades(courses=[phys_course, hist_course])}
    assert "error" in rows["PHYS1100"]
    assert rows["PHYS1100"].get("current_score") is None
    assert rows["HIST2200"]["current_score"] == 91.5, "one hidden class must not sink the rest"


def test_grades_can_skip_the_per_assignment_call(hist_course):
    """items=False exists so a totals-only question doesn't pay for a second
    paginated request per course."""
    row = canvas.grades(courses=[hist_course], items=False)[0]
    assert "items" not in row
    assert row["current_score"] == 91.5


def test_pass_fail_work_keeps_its_result(hist_course):
    """Complete/incomplete assignments carry the result in `grade`, not `score`.
    Reading only `score` rendered a marked assignment as unmarked."""
    from tests.conftest import FakeSubmission
    sub = FakeSubmission("Reflection", None, None, "2026-09-20T10:00:00Z")
    sub.grade = "complete"
    hist_course._submissions = [sub]
    item = canvas.grades(courses=[hist_course])[0]["items"][0]
    assert item["grade"] == "complete"
    assert item["status"] == "graded", "a complete/incomplete mark is a grade"


def test_graded_with_no_mark_is_not_called_graded(hist_course):
    """Canvas returns workflow_state=graded with neither score nor grade set.
    Reporting that as "graded" implies a mark the student hasn't actually got."""
    from tests.conftest import FakeSubmission
    sub = FakeSubmission("Assessment", None, 10, "2026-09-20T10:00:00Z", "graded")
    hist_course._submissions = [sub]
    assert canvas.grades(courses=[hist_course])[0]["items"][0]["status"] == "no mark recorded"


def test_excused_work_is_labelled_excused(hist_course):
    """Excused is not a zero and not a pending mark."""
    from tests.conftest import FakeSubmission
    sub = FakeSubmission("Quiz 3", None, 10, None, "graded")
    sub.excused = True
    hist_course._submissions = [sub]
    assert canvas.grades(courses=[hist_course])[0]["items"][0]["status"] == "excused"


# --- grades are opt-in over MCP ------------------------------------------------

def _mcp_with(monkeypatch, value):
    """Import mcp_server fresh with CANVAS_ENABLE_GRADES set to `value`."""
    import importlib, sys as _s
    if value is None:
        monkeypatch.delenv("CANVAS_ENABLE_GRADES", raising=False)
    else:
        monkeypatch.setenv("CANVAS_ENABLE_GRADES", value)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)  # ignore a real .env
    _s.modules.pop("canvas_vault.mcp_server", None)
    return importlib.import_module("canvas_vault.mcp_server")


def test_grades_tool_absent_unless_enabled(monkeypatch):
    """Not registered rather than registered-and-refusing: a tool the model cannot
    see cannot leak, and it costs no tokens in the tool list."""
    assert not hasattr(_mcp_with(monkeypatch, None), "grades")
    assert not hasattr(_mcp_with(monkeypatch, "0"), "grades")
    assert not hasattr(_mcp_with(monkeypatch, "false"), "grades")


def test_grades_tool_present_when_enabled(monkeypatch):
    for value in ("1", "true", "YES", "on"):
        assert hasattr(_mcp_with(monkeypatch, value), "grades"), value


def test_local_grades_are_never_gated(monkeypatch):
    """Only the MCP surface is gated. Running the CLI on your own machine sends
    nothing anywhere, so gating it would be security theatre."""
    monkeypatch.delenv("CANVAS_ENABLE_GRADES", raising=False)
    assert callable(canvas.grades)
    assert callable(canvas.cmd_grades)


def test_locked_files_are_skipped_not_fatal(tmp_path, monkeypatch, phys_course):
    """Instructors gate lectures behind module dates. Canvas returns full
    metadata with locked_for_user set and an EMPTY url, so download() raises
    ResourceDoesNotExist — which once aborted the entire course ingest, so every
    file queued after the locked one was skipped too."""
    from tests.conftest import FakeFile, FakeModule, FakeModuleItem
    locked = FakeFile(7003, "Lecture2.pdf", locked=True)
    phys_course._files.append(locked)
    phys_course._modules.append(FakeModule("L2", [FakeModuleItem("Lecture2.pdf", 7003)]))
    assert canvas.is_locked(locked)
    assert not canvas.is_locked(FakeFile(7004, "Lecture3.pdf"))

    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes" / "PHYS1100").mkdir(parents=True)
    c = Course.from_canvas(phys_course)
    monkeypatch.setattr(type(c), "_api", property(lambda self: phys_course))
    pending = c.pending_files()
    assert any(p.startswith("Lecture2.pdf") and "not released" in p for p in pending), pending
    assert "Lecture1-Newton.pptx" in pending, "unlocked files still listed plainly"


def test_locked_label_does_not_break_the_already_have_check(tmp_path, monkeypatch, phys_course):
    """The label is presentation only. Folding it into the name before taking
    Path().stem would break the lookup against notes already on disk."""
    from tests.conftest import FakeFile, FakeModule, FakeModuleItem
    phys_course._files.append(FakeFile(7003, "Lecture2.pdf", locked=True))
    phys_course._modules.append(FakeModule("L2", [FakeModuleItem("Lecture2.pdf", 7003)]))
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "notes" / "PHYS1100"
    d.mkdir(parents=True)
    (d / "Lecture2.md").write_text("already transcribed")
    c = Course.from_canvas(phys_course)
    monkeypatch.setattr(type(c), "_api", property(lambda self: phys_course))
    assert not any("Lecture2" in p for p in c.pending_files()), \
        "a note already on disk must not be reported as pending"


# --- courses that live on the professor's own website -------------------------

def test_signpost_syllabus_yields_the_course_site(hist_course):
    """Some instructors keep everything on their own page and leave the Canvas
    syllabus as one line pointing at it. Canvas then has no files and no modules,
    so the course looks empty when it is not."""
    from canvas_vault import ingest
    hist_course.syllabus_body = (
        '<p>All materials and information at: '
        '<a href="https://prof.example.com/ds4440/">https://prof.example.com/ds4440/</a></p>')
    assert ingest.external_site(hist_course) == "https://prof.example.com/ds4440/"


def test_a_real_syllabus_that_links_out_is_not_a_signpost(hist_course):
    """A full syllabus links to Piazza and a textbook. Following those would
    ingest the internet instead of the course."""
    from canvas_vault import ingest
    hist_course.syllabus_body = (
        '<p>Join <a href="https://piazza.com/x">Piazza</a>.</p>' +
        "<p>Late work loses 10% per day. </p>" * 60)
    assert ingest.external_site(hist_course) is None


def test_canvas_own_assets_are_not_mistaken_for_the_course_site(hist_course):
    """Canvas injects its own stylesheet URLs into syllabus_body."""
    from canvas_vault import ingest
    hist_course.syllabus_body = (
        '<link href="https://instructure-uploads.s3.amazonaws.com/a/dp_app.css">'
        '<p>See <a href="https://prof.example.com/course/">the site</a></p>')
    assert ingest.external_site(hist_course) == "https://prof.example.com/course/"


def test_site_links_stay_on_the_professors_own_page():
    """A course page links to arXiv, Colab, textbooks and blogs. None of that is
    the student's course material, and following it turns a sync into a crawl."""
    from canvas_vault import ingest
    page = "https://prof.example.com/ds4440/"
    body = '''
      <a href="lecture-materials/l1-slides.pdf">Slides</a>
      <a href="lecture-materials/l1-notes.pdf">Notes</a>
      <a href="https://colab.research.google.com/drive/abc">HW 1</a>
      <a href="https://www.nature.com/articles/323533a0.pdf">Rumelhart 1986</a>
      <a href="https://prof.example.com/other-course/secret.pdf">Another course</a>
      <a href="https://d2l.ai/chapter/index.html">Textbook</a>
      <a href="lecture-materials/l1-slides.pdf">Slides again</a>
    '''
    got = dict(ingest.site_links(page, body))
    assert set(got.values()) == {"l1-slides.pdf", "l1-notes.pdf"}, got
    assert not any("nature.com" in u or "colab" in u for u in got), "must not leave the host"
    assert not any("other-course" in u for u in got), "must stay under the course path"


def test_gold_sets_are_per_course():
    """A single shared eval_queries.json meant generating a set for a second
    class silently destroyed the first one's pooled relevance labels, which cost
    real API calls to build."""
    import importlib.util, pathlib as _p
    spec = importlib.util.spec_from_file_location(
        "make_eval_set", _p.Path(__file__).parent.parent / "tools" / "make_eval_set.py")
    mes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mes)
    assert mes.out_path("DS4400") != mes.out_path("DS4440")
    assert "DS4440" in str(mes.out_path("DS4440"))
