import pytest
from pathlib import Path

from canvas_vault.extract import graph_data, concept_data

SLUG = "DS4400"
pytestmark = pytest.mark.skipif(not (Path("vault") / SLUG / "concepts").exists(),
                                reason="run extract.py <slug> first")


def test_graph_data_shape():
    g = graph_data(SLUG)
    assert len(g["nodes"]) > 10 and len(g["edges"]) > 10
    assert set(g["nodes"][0]) == {"id", "lect", "degree"}


def test_concept_data_known():
    c = concept_data(SLUG, "Gradient Descent")
    assert c and c["definition"] and c["lectures"]
    assert concept_data(SLUG, "Nonexistent Concept XYZ") is None
