from fastapi.testclient import TestClient
import pytest
from pathlib import Path
from server import app

client = TestClient(app)
pytestmark = pytest.mark.skipif(not Path("vault/concepts").exists(),
                                reason="run extract.py first")

def test_graph_endpoint():
    r = client.get("/api/graph")
    assert r.status_code == 200 and len(r.json()["nodes"]) > 10

def test_concept_endpoint():
    r = client.get("/api/concept/Gradient Descent")
    assert r.status_code == 200 and r.json()["definition"]
    assert client.get("/api/concept/Nope XYZ").status_code == 404
