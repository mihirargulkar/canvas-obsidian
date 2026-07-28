import pytest
from extract import graph_data, concept_data
from pathlib import Path

pytestmark = pytest.mark.skipif(not Path("vault/concepts").exists(),
                                reason="run extract.py first")

def test_graph_data_shape():
    g = graph_data()
    assert len(g["nodes"]) > 10 and len(g["edges"]) > 10
    n = g["nodes"][0]
    assert set(n) == {"id", "lect", "degree"}

def test_concept_data_known():
    c = concept_data("Gradient Descent")
    assert c and c["definition"] and c["lectures"]
    assert concept_data("Nonexistent Concept XYZ") is None
