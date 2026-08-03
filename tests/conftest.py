"""Synthetic courses, so the pipeline can be tested without Canvas or an API key.

Most of this project talks to a live LMS and a vision model, which made whole
modules untestable: they were exercised only by running a real sync against one
real account. These fixtures stand in for both, so the pipeline can be driven
end to end offline and in CI.

Two courses on purpose. PHYS1100 is technical, HIST2200 is not, which keeps us
honest about the tool being subject agnostic (the extraction prompt has ML
flavoured examples in it, and it would be easy to quietly grow an assumption
that every course looks like machine learning).
"""
import hashlib

import numpy as np
import pytest

# --- fake Canvas objects ------------------------------------------------------


class FakeAssignment:
    def __init__(self, name, due_at=None, points=None, description=""):
        self.name = name
        self.due_at = due_at
        self.points_possible = points
        self.description = description

    def __getattr__(self, _):        # any other Canvas field
        return None


class FakeFile:
    def __init__(self, id, display_name, updated_at="2026-07-01T00:00:00Z", size=1024):
        self.id, self.display_name = id, display_name
        self.updated_at, self.size = updated_at, size


class FakeAnnouncement:
    def __init__(self, title, message, posted_at):
        self.title, self.message, self.posted_at = title, message, posted_at


class FakeCourse:
    """Stands in for a canvasapi Course. `forbid` makes a tab raise, the way an
    instructor-restricted Files or Assignments tab does."""

    def __init__(self, id, name, course_code="", assignments=(), files=(),
                 announcements=(), syllabus_body="", forbid=()):
        self.id, self.name, self.course_code = id, name, course_code
        self.syllabus_body = syllabus_body
        self._assignments, self._files = list(assignments), list(files)
        self._announcements = list(announcements)
        self._forbid = set(forbid)

    def _check(self, what):
        if what in self._forbid:
            raise PermissionError('{"status":"unauthorized"}')

    def get_assignments(self):
        self._check("assignments")
        return list(self._assignments)

    def get_files(self):
        self._check("files")
        return list(self._files)

    def get_discussion_topics(self, only_announcements=False):
        self._check("announcements")
        return list(self._announcements)


@pytest.fixture
def phys_course():
    """A technical course with a restricted Files tab, which is common."""
    return FakeCourse(
        id=1001, name="PHYS 1100 Classical Mechanics", course_code="PHYS1100.1.202610",
        assignments=[
            FakeAssignment("Problem Set 1", "2026-09-05T03:59:00Z", 50),
            FakeAssignment("Midterm", "2026-10-01T15:00:00Z", 100),
            FakeAssignment("Attendance", None, 10),          # undated, must be skipped
        ],
        announcements=[
            FakeAnnouncement("Lab moved to Friday", "<p>Lab is <b>Friday</b> this week.</p>",
                             "2026-09-02T12:00:00Z"),
        ],
        syllabus_body="<p>Late work loses 10% per day.</p>",
        forbid=["files"],
    )


@pytest.fixture
def hist_course():
    """A humanities course, to keep the pipeline honest about being general."""
    return FakeCourse(
        id=1002, name="HIST 2200 Modern Europe", course_code="HIST2200.7.202610",
        assignments=[FakeAssignment("Essay 1", "2026-09-10T03:59:00Z", 25)],
        files=[FakeFile(9001, "Lecture1-Revolutions.pptx"),
               FakeFile(9002, "readings.csv")],           # not ingestible, not "missing"
        announcements=[FakeAnnouncement("Reading list posted", "<p>See the syllabus.</p>",
                                        "2026-09-01T09:00:00Z")],
    )


# --- synthetic notes on disk --------------------------------------------------

PHYS_LECTURE = """---
source: Lecture1-Newton.pptx
course: PHYS1100
---

# Lecture 1 - Newton's Laws

## Newton's First Law
An object in motion stays in motion unless acted on by an external force. This is
also called the law of inertia, and it defines what an inertial reference frame is.

## Newton's Second Law
Force equals mass times acceleration, $F = ma$. This is the workhorse equation for
solving mechanics problems and it builds directly on the idea of inertia.

## Momentum
Momentum is mass times velocity. In a closed system total momentum is conserved,
which follows from the second law applied to an isolated collection of bodies.
"""

HIST_LECTURE = """---
source: Lecture1-Revolutions.pptx
course: HIST2200
---

# Lecture 1 - The Age of Revolutions

## The Estates General
The Estates General was the assembly summoned in 1789, dividing representation
between clergy, nobility and commoners, and its deadlock triggered wider upheaval.

## The Terror
The Terror was the period of mass executions following the revolution, driven by
the Committee of Public Safety and justified as defence of the republic.
"""


@pytest.fixture
def synthetic_notes(tmp_path, monkeypatch):
    """A notes/ tree for two courses, with the cwd pointed at it."""
    monkeypatch.chdir(tmp_path)
    for slug, body, extra in [("PHYS1100", PHYS_LECTURE, "hw-ProblemSet1"),
                              ("HIST2200", HIST_LECTURE, "hw-Essay1")]:
        d = tmp_path / "notes" / slug
        d.mkdir(parents=True)
        (d / "Lecture1.md").write_text(body)
        # non-lecture material: searchable, but must stay out of the concept graph
        (d / f"{extra}.md").write_text(
            f"---\nsource: {extra}.pdf\n---\n\n## {extra}\n\n"
            "Answer every question and show your working. Due at the end of the week.\n")
    return tmp_path


# --- a deterministic embedder -------------------------------------------------


@pytest.fixture
def stub_embed():
    """Hashed bag of words instead of a real model.

    Tests shouldn't download a 30MB model or depend on its behaviour. This is
    deterministic, instant, and still gives genuine lexical similarity, which is
    all the storage and retrieval plumbing needs to be exercised.
    """
    dim = 64

    def encode(texts):
        out = np.zeros((len(texts), dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in text.lower().split():
                h = int(hashlib.md5(word.encode()).hexdigest()[:8], 16)
                out[row, h % dim] += 1.0
        return out

    return encode
