"""Course domain-object tests — pure identity/path logic, no network."""
from pathlib import Path

from canvas_vault.course import Course


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


def test_slug_prefers_course_code():
    c = Course(id=1, name="DS 4400 Machine Learning", code="DS4400.50397.202650")
    assert c.slug == "DS4400"


def test_slug_joins_space_separated_code():
    # "DS 4400 ..." must not become "DS", which would collide with every other
    # DS course the student takes
    assert Course(id=1, name="DS 4400 Machine Learning").slug == "DS4400"
    assert Course(id=2, name="CS 3200 Databases").slug == "CS3200"


def test_slug_is_filesystem_safe():
    # cross-listed names contain "/", and a slug is used as a path component
    assert "/" not in Course(id=1, name="CS1800/1802 Discrete").slug
    assert ".." not in Course(id=2, name="../../etc/passwd oops").slug


def test_slug_survives_empty_name():
    assert Course(id=42, name="").slug == "course-42"


def test_colliding_slugs_are_disambiguated():
    # two sections of one class would otherwise share notes/<slug> and merge
    out = Course._disambiguate([Course(id=1, name="DS4400 Lecture", code="DS4400.1.X"),
                                Course(id=2, name="DS4400 Lab", code="DS4400.2.X"),
                                Course(id=3, name="ORGB3201 OB", code="ORGB3201.9.X")])
    assert [c.slug for c in out] == ["DS4400-1", "DS4400-2", "ORGB3201"]
