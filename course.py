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

from dataclasses import dataclass
from pathlib import Path

import canvas as canvas_api


@dataclass(frozen=True)
class Course:
    """One Canvas course. `slug` (e.g. 'DS4400') keys all of its data on disk."""

    id: int
    name: str

    # --- construction -----------------------------------------------------

    @classmethod
    def from_canvas(cls, c) -> "Course":
        return cls(id=c.id, name=canvas_api.course_label(c))

    @classmethod
    @canvas_api.ttl_cache(300)
    def current(cls) -> list["Course"]:
        """Every course in the most recent term (all your classes this semester).
        Cached briefly — every MCP tool call resolves a slug through this."""
        client = canvas_api.get_client()
        return [cls.from_canvas(c) for c in canvas_api.current_courses(client)]

    @classmethod
    def get(cls, course_id: int) -> "Course":
        return cls.from_canvas(canvas_api.get_client().get_course(course_id))

    # --- identity & paths -------------------------------------------------

    @property
    def slug(self) -> str:
        """Short key, e.g. 'DS4400' — first token of the course name."""
        return self.name.split()[0]

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
        import ingest
        return ingest.ingest_course(self.id, limit)

    def extract(self):
        """Lecture notes -> concept graph -> vault/<slug>/concepts/."""
        import extract
        return extract.build(self.slug)

    def refresh_updates(self):
        """Announcements + syllabus -> notes/<slug>/ and vault/<slug>/updates/."""
        import updates
        data = updates.fetch_updates(self.id)
        updates.write_notes(self.slug, data)
        return data

    def dashboard(self, days: int = 14):
        """vault/<slug>/Dashboard.md — this class's deadlines + announcements."""
        import dashboard
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
        import updates
        return updates.fetch_updates(self.id)["announcements"][:limit]

    def syllabus(self) -> str:
        import updates
        return updates.fetch_updates(self.id)["syllabus"]

    def concept(self, name: str) -> dict | None:
        import extract
        return extract.concept_data(self.slug, name)

    def graph(self) -> dict:
        import extract
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
        import changes
        import updates as updates_mod
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
