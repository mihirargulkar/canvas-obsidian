"""Course domain-object tests — pure identity/path logic, no network."""
from pathlib import Path

from course import Course


def test_slug_and_paths():
    c = Course(id=253025, name="DS4400 50397 Machine Learning/Data Mining 1 SEC 01")
    assert c.slug == "DS4400"                       # first token of the course name
    assert c.notes_dir == Path("notes/DS4400")
    assert c.vault_dir == Path("vault/DS4400")
    assert "DS4400" in str(c) and "253025" in str(c)


def test_courses_are_value_objects():
    a = Course(id=1, name="ORGB3201 51153 Organizational Behavior")
    b = Course(id=1, name="ORGB3201 51153 Organizational Behavior")
    assert a == b and hash(a) == hash(b)            # frozen dataclass: usable in sets/dicts
    assert a.slug == "ORGB3201"
    assert len({a, b}) == 1
