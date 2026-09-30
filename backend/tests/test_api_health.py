"""API health endpoints."""

from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_exact_payload():
    with TestClient(app) as client:
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "service": "specengine-bis"}


def test_health_does_not_depend_on_database():
    """Liveness must stay truthful even if the DB layer is broken."""
    with TestClient(app) as client:
        # No DB assertion here — this endpoint must never 500 because of DB.
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200


def test_database_health_check():
    with TestClient(app) as client:
        resp = client.get("/api/v1/health/db")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["service"] == "specengine-bis"
        assert body["database"] == "ok"


def test_stats_on_empty_database():
    """Real zeros from a real query — not a fabricated success body."""
    with TestClient(app) as client:
        resp = client.get("/api/v1/stats")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["sources"] == 0
        assert body["documents"] == 0
        assert body["clause_nodes"] == 0
        assert body["chunks"] == 0
        assert body["by_source_type"] == []


def test_search_with_query_hits_the_cast_expression():
    """Exercises the fixed sqlalchemy cast() in the search WHERE clause."""
    with TestClient(app) as client:
        resp = client.get("/api/v1/search", params={"q": "steel"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["results"] == []


def test_search_without_query():
    with TestClient(app) as client:
        resp = client.get("/api/v1/search")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
