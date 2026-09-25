"""Review point MINEUR: the dashboard binds 0.0.0.0 with no auth. Optional
HTTP Basic auth, enabled only when DASHBOARD_USER and DASHBOARD_PASSWORD are set."""
import base64

from fastapi.testclient import TestClient

from dashboard.app import app


def _hdr(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def test_no_credentials_configured_means_no_auth(monkeypatch):
    monkeypatch.setattr("dashboard.app._dashboard_credentials", lambda: None)
    assert TestClient(app).get("/healthz").status_code != 401


def test_credentials_configured_requires_them(monkeypatch):
    monkeypatch.setattr("dashboard.app._dashboard_credentials", lambda: ("florian", "s3cret"))
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 401 and "Basic" in r.headers["www-authenticate"]
    assert client.get("/", headers=_hdr("florian", "wrong")).status_code == 401
    assert client.get("/healthz", headers=_hdr("florian", "s3cret")).status_code != 401
