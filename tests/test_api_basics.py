from __future__ import annotations

from .conftest import auth_headers


def test_health_is_public_and_business_api_requires_token(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database"]["ok"] is True
    assert payload["database"]["fts5"] is True
    assert payload["auth_required"] is True

    response = client.get("/api/v1/memories")
    assert response.status_code == 401

    response = client.get("/api/v1/memories", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401

    response = client.get("/api/v1/memories", headers=auth_headers())
    assert response.status_code == 200


def test_origin_check_rejects_unknown_browser_origin(client):
    headers = {**auth_headers(), "Origin": "https://evil.example"}
    response = client.get("/api/v1/memories", headers=headers)
    assert response.status_code == 403

    allowed = {**auth_headers(), "Origin": "http://localhost:5173"}
    response = client.get("/api/v1/memories", headers=allowed)
    assert response.status_code == 200


def test_metadata_and_config_do_not_leak_secrets(client):
    response = client.get("/api/v1/meta", headers=auth_headers())
    assert response.status_code == 200
    data = response.json()["data"]
    assert "episodic" in data["memory_types"]
    assert "contradicts" in data["relation_types"]
    assert data["access_modes"] == ["manage", "recall"]

    response = client.get("/api/v1/config", headers=auth_headers())
    assert response.status_code == 200
    config = response.json()["data"]
    assert "api_key_encrypted" not in str(config)
    assert config["embedding"]["api_key_set"] is False
    assert "security" in config


def test_spaces_are_created_lazily_and_listed(client):
    response = client.post(
        "/api/v1/memories",
        headers=auth_headers(),
        json={"content": "用户喜欢茉莉花茶", "category": "preference"},
    )
    assert response.status_code == 200
    space_id = response.json()["data"]["memory"]["space_id"]

    response = client.get("/api/v1/spaces", headers=auth_headers())
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert any(item["id"] == space_id for item in items)
    item = next(entry for entry in items if entry["id"] == space_id)
    assert item["kind"] == "user"
    assert item["memory_count"] == 1


def test_unknown_api_path_returns_json_error(client):
    response = client.get("/api/v1/does-not-exist", headers=auth_headers())
    assert response.status_code == 404
    assert response.json()["status"] == "error"
    assert response.headers.get("X-Request-ID")
