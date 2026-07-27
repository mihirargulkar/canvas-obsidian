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
