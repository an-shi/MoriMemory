from __future__ import annotations

from .conftest import auth_headers


def _create(client, headers, content, **extra):
    response = client.post("/api/v1/memories", headers=headers, json={"content": content, **extra})
    assert response.status_code == 200, response.text
    return response.json()["data"]["memory"]["id"]


def test_graph_projection_path_and_local_expansion(client):
    headers = auth_headers()
    first = _create(client, headers, "图谱节点甲", importance=0.9)
    second = _create(client, headers, "图谱节点乙", importance=0.7)
    third = _create(client, headers, "图谱节点丙", importance=0.5)

    for source, target in ((first, second), (second, third)):
        response = client.post(
            "/api/v1/relations",
            headers=headers,
            json={
                "source_id": source,
                "target_id": target,
                "relation_type": "related_to",
                "strength": 0.8,
                "confidence": 0.9,
            },
        )
        assert response.status_code == 200, response.text

    graph = client.get("/api/v1/graph", headers=headers).json()["data"]
    assert len(graph["nodes"]) == 3
    assert len(graph["edges"]) == 2
    assert {node["id"] for node in graph["nodes"]} == {first, second, third}
    assert all("space_kind" in node for node in graph["nodes"])

    path = client.get(
        f"/api/v1/graph/path?source_id={first}&target_id={third}", headers=headers
    ).json()["data"]
    assert path["found"] is True
    assert path["depth"] == 2
    assert path["node_ids"] == [first, second, third]

    local = client.get(f"/api/v1/graph?center_id={first}&depth=1", headers=headers).json()["data"]
    assert {node["id"] for node in local["nodes"]} == {first, second}
    assert len(local["edges"]) == 1

    missing = client.get(f"/api/v1/graph/path?source_id={first}&target_id=missing", headers=headers)
    assert missing.status_code == 404


def test_graph_filters_by_type_and_time(client):
    headers = auth_headers()
    _create(client, headers, "偏好类图谱记忆", category="preference", memory_type="long_term")
    _create(client, headers, "事件类图谱记忆", category="event", memory_type="episodic")
    graph = client.get("/api/v1/graph?memory_type=episodic", headers=headers).json()["data"]
    assert len(graph["nodes"]) == 1
    assert graph["nodes"][0]["memory_type"] == "episodic"
