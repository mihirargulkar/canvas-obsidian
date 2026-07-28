"""Self-check for canvas.py pure logic. Run: python test_canvas.py  (no network)."""
from datetime import datetime, timezone
from canvas import term_code, parse_due, in_window

# term code extraction
assert term_code("202650_2B Summer 2026 Semester Session B") == "202650"
assert term_code("Group Courses Term") is None
assert term_code(None) is None
# max() picks the current term correctly across the real term set
assert max(filter(None, [term_code("202410_1 Fall 2023"),
                         term_code("202650_2B Summer 2026"),
                         term_code("202610_1 Fall 2025")])) == "202650"

# ISO parsing (handles trailing Z)
assert parse_due("2026-07-27T03:59:59Z") == datetime(2026, 7, 27, 3, 59, 59, tzinfo=timezone.utc)
assert parse_due(None) is None
assert parse_due("") is None

# window filter
now = datetime(2026, 7, 27, 0, 0, tzinfo=timezone.utc)
assert in_window("2026-07-28T03:59:59Z", now, 7) is True      # inside
assert in_window("2026-08-30T00:00:00Z", now, 7) is False     # past window
assert in_window("2026-07-01T00:00:00Z", now, 7) is False     # already past
assert in_window(None, now, 7) is False                       # undated excluded

print("test_canvas.py: all asserts passed")

# --- test upcoming() helper
from canvas import upcoming
from datetime import timedelta

class _FakeA:
    def __init__(self, due_at, name, points_possible=None):
        self.due_at, self.name, self.points_possible = due_at, name, points_possible
    def __getattr__(self, k): return None            # other attributes
class _FakeC:
    def __init__(self, name, assigns): self.name, self._a = name, assigns
    def get_assignments(self): return self._a
    def __getattr__(self, k): return None            # for id, term, etc.

def test_upcoming_filters_and_sorts():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    soon = (now + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    later = (now + timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    courses = [_FakeC("DS4400 Machine Learning", [
        _FakeA(later, "Far"), _FakeA(soon, "Near"), _FakeA(None, "Undated")])]
    rows = upcoming(14, courses=courses)
    assert [r[2] for r in rows] == ["Near"]          # far + undated excluded
    assert rows[0][1] == "DS4400"                     # course code = first token
    assert len(rows[0]) == 4                          # (due, course, name, points)

test_upcoming_filters_and_sorts()
print("test_upcoming_filters_and_sorts passed")
