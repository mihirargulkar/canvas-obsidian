#!/usr/bin/env python3
"""Course — the domain object for one Canvas class.

Owns the identity (canvas id, slug) and the paths that identity implies, and
exposes the pipeline as behaviour:

    for c in Course.current():      # every class you're enrolled in this term
        c.sync()                    # ingest -> extract -> updates -> dashboard

The heavy lifting stays in the pipeline modules (ingest/extract/updates/
dashboard) as functions; Course composes them so no caller has to thread a
`slug` through every call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from . import canvas as canvas_api


@dataclass(frozen=True)
class Course:
    """One Canvas course. `slug` (e.g. 'DS4400') keys all of its data on disk."""

    id: int
    name: str
    code: str = ""          # Canvas course_code, e.g. "DS4400.50397.202650"

    # --- construction -----------------------------------------------------

    @classmethod
    def from_canvas(cls, c) -> "Course":
        return cls(id=c.id, name=canvas_api.course_label(c),
                   code=getattr(c, "course_code", "") or "")

    @classmethod
    @canvas_api.ttl_cache(300)
    def current(cls) -> list["Course"]:
        """Every course in the most recent term (all your classes this semester).
        Cached briefly — every MCP tool call resolves a slug through this."""
        client = canvas_api.get_client()
        return cls._disambiguate(
            [cls.from_canvas(c) for c in canvas_api.current_courses(client)])

    @staticmethod
    def _disambiguate(courses: list["Course"]) -> list["Course"]:
        """Ensure slugs are unique. Two courses that key to the same directory
        would silently merge each other's notes (e.g. two sections of one class,
        or a lecture/lab pair), so a collision gets the course id appended."""
        from collections import Counter
        dupes = {s for s, n in Counter(c.slug for c in courses).items() if n > 1}
        return [replace(c, code=f"{c.slug}-{c.id}") if c.slug in dupes else c
                for c in courses]

    @classmethod
    def get(cls, course_id: int) -> "Course":
        return cls.from_canvas(canvas_api.get_client().get_course(course_id))

    # --- identity & paths -------------------------------------------------

    @property
    def slug(self) -> str:
        """Filesystem-safe short key for this course, e.g. 'DS4400'.

        Prefers Canvas's course_code ("DS4400.50397.202650" -> "DS4400"), which is
        reliable. Falls back to parsing the name, joining a leading letters+digits
        pair so "DS 4400 Machine Learning" gives "DS4400" rather than "DS" (which
        would collide with every other DS course the student takes).

        Always sanitised: a name may contain "/" (cross-listed courses like
        "CS1800/1802") or "..", either of which would escape or nest the data
        directory when used as a path component.
        """
        raw = (self.code or "").split(".")[0].strip()
        if not raw:
            tokens = self.name.split()
            if tokens:
                # "DS 4400 ..." -> "DS4400"; otherwise just the first token
                raw = (tokens[0] + tokens[1]
                       if len(tokens) > 1 and tokens[0].isalpha() and tokens[1][:1].isdigit()
                       else tokens[0])
        safe = re.sub(r"[^A-Za-z0-9_-]", "", raw)
        return safe or f"course-{self.id}"

    @property
    def notes_dir(self) -> Path:
        return Path("notes") / self.slug

    @property
    def vault_dir(self) -> Path:
        return Path("vault") / self.slug

    @property
    def _api(self):
        """Live canvasapi Course object (module-level TTL cache, so repeated
        Course instances for the same class share one fetch)."""
        return canvas_api.get_course(self.id)

    # --- pipeline ---------------------------------------------------------

    def ingest(self, limit=None):
        """Canvas files + homework -> notes/<slug>/*.md (Gemini vision + text)."""
        from . import ingest
        return ingest.ingest_course(self.id, limit)

    def extract(self):
        """Lecture notes -> concept graph -> vault/<slug>/concepts/."""
        from . import extract
        return extract.build(self.slug)

    def refresh_updates(self):
        """Announcements + syllabus -> notes/<slug>/ and vault/<slug>/updates/."""
        from . import updates
        data = updates.fetch_updates(self.id)
        updates.write_notes(self.slug, data)
        return data

    def dashboard(self, days: int = 14):
        """vault/<slug>/Dashboard.md — this class's deadlines + announcements."""
        from . import dashboard
        return dashboard.course_dashboard(self, days)

    # --- reads (used by the MCP tools) -----------------------------------

    def upcoming(self, days: int = 7) -> list[tuple]:
        """This course's assignments due within `days`.

        Scoped to this course only — asking canvas_api.upcoming() unscoped would
        fetch every class's assignments just to filter them away (O(classes^2)
        across a multi-class sync).
        """
        return canvas_api.upcoming(days, courses=[self._api])

    def announcements(self, limit: int = 10) -> list[dict]:
        from . import updates
        return updates.fetch_updates(self.id)["announcements"][:limit]

    def syllabus(self) -> str:
        from . import updates
        return updates.fetch_updates(self.id)["syllabus"]

    def concept(self, name: str) -> dict | None:
        from . import extract
        return extract.concept_data(self.slug, name)

    def graph(self) -> dict:
        from . import extract
        return extract.graph_data(self.slug)

    # --- orchestration ----------------------------------------------------

    def sync(self, limit=None, deep=True) -> list[str]:
        """Full pipeline for this one class. Safe to re-run (content-hash cached).

        Each step is isolated: a course with Files disabled (Canvas 403) or a
        rate-limited step degrades that step only — the rest of this class, and
        every other class, still sync. Returns the names of failed steps.
        """
        print(f"\n=== {self.slug} — {self.name} ===")
        failed = []
        # deep=False skips the slow model work (file transcription, concept
        # extraction) and only re-reads Canvas metadata. That's what an
        # interactive "anything new?" check needs — a full sync takes minutes and
        # will blow an MCP client's request timeout.
        steps = (("updates", self.refresh_updates), ("dashboard", self.dashboard))
        if deep:
            steps = (("ingest", lambda: self.ingest(limit)), ("updates", self.refresh_updates),
                     ("extract", self.extract), ("dashboard", self.dashboard))
        for step, fn in steps:
            try:
                fn()
            except Exception as e:
                failed.append(step)
                reason = ("not permitted for this course (instructor restricted it)"
                          if "unauthorized" in str(e).lower() or "Forbidden" in type(e).__name__
                          else f"{type(e).__name__}: {str(e)[:80]}")
                print(f"  ! {step} skipped — {reason}")
        return failed

    def changes_since_last_sync(self):
        """What appeared on Canvas since the previous sync (see changes.py).

        Re-reads announcements/assignments rather than caching them on the
        instance — Course is frozen, and both reads are TTL-cached anyway.
        """
        from . import changes
        from . import updates as updates_mod
        try:
            anns = updates_mod.fetch_updates(self.id).get("announcements", [])
        except Exception:
            anns = []
        try:
            names = [a.name for a in self._api.get_assignments()]
        except Exception:
            names = []
        return changes.diff_course(self.slug, anns, names)

    def __str__(self):
        return f"{self.slug} ({self.id})"
