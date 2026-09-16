# tests/test_dashboard_app.py
from fastapi.testclient import TestClient

from dashboard.app import app

client = TestClient(app)


def test_root_page_shows_simulation_banner():
    response = client.get("/")
    assert response.status_code == 200
    assert "⚠ SIMULATION — Aucun argent réel, aucun ordre réel envoyé" in response.text
