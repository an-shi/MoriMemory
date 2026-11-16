from __future__ import annotations

import sqlite3

from .conftest import auth_headers


def create_memory(client, headers, **payload):
    response = client.post("/api/v1/memories", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_memory_crud_evidence_versions_and_fts(client):
    headers = auth_headers()
    created = create_memory(
        client,
        headers,
        content="用户喜欢喝龙井茶",
        title="饮茶偏好",
        category="preference",
        tags=["茶", "偏好"],
        importance=0.8,
        confidence=0.95,
    )
    memory = created["memory"]
    memory_id = memory["id"]
    assert created["action"] == "created"
    assert memory["source_kind"] == "user_explicit"
    assert memory["importance"] == 0.8

    detail = client.get(f"/api/v1/memories/{memory_id}", headers=headers).json()["data"]
    assert detail["evidence"][0]["quote"] == "用户喜欢喝龙井茶"
    assert detail["evidence"][0]["attributes"]["synthetic"] is True
    assert detail["versions"][0]["version"] == 1

    response = client.patch(
        f"/api/v1/memories/{memory_id}",
        headers=headers,
        json={"content": "用户喜欢喝乌龙茶", "importance": 0.9},
    )
    assert response.status_code == 200, response.text
    updated = response.json()["data"]
    assert updated["content"] == "用户喜欢喝乌龙茶"
    assert updated["version"] == 2
    assert len(updated["versions"]) == 2

    old_query = client.post(
        "/api/v1/search",
        headers=headers,
        json={"query": "龙井", "mode": "manage", "record_access": False},
    ).json()["data"]["items"]
    assert old_query == []

    new_query = client.post(
        "/api/v1/search",
        headers=headers,
        json={"query": "乌龙", "mode": "manage", "record_access": False},
    ).json()["data"]["items"]
    assert len(new_query) == 1
    assert new_query[0]["id"] == memory_id

    evidence_response = client.post(
        f"/api/v1/memories/{memory_id}/evidence",
        headers=headers,
        json={
            "source_kind": "user_explicit",
            "conversation_id": "conv1",
            "message_id": "m-extra",
            "role": "user",
            "quote": "我最近改喝乌龙茶了",
            "reinforce": True,
        },
    )
    assert evidence_response.status_code == 200, evidence_response.text
    detail = client.get(f"/api/v1/memories/{memory_id}", headers=headers).json()["data"]
    assert len(detail["evidence"]) == 2
    assert detail["reinforcement_count"] == 2

    archived = client.post(f"/api/v1/memories/{memory_id}/archive", headers=headers)
    assert archived.json()["data"]["status"] == "archived"
    restored = client.post(f"/api/v1/memories/{memory_id}/restore", headers=headers)
    assert restored.json()["data"]["status"] == "active"


def test_exact_duplicate_reinforces_instead_of_creating(client):
    headers = auth_headers()
    first = create_memory(client, headers, content="我住在上海", category="fact")
    second = create_memory(client, headers, content="我住在上海", category="fact")
    assert second["action"] == "reinforced"
    assert second["duplicate_of"] == first["memory"]["id"]
    assert second["memory"]["reinforcement_count"] == 2

    listing = client.get("/api/v1/memories", headers=headers).json()["data"]
    assert listing["total"] == 1


def test_conflicting_slot_creates_contradiction_relation(client):
    headers = auth_headers()
    first = create_memory(client, headers, content="我住在上海", category="fact")
    second = create_memory(client, headers, content="我住在北京", category="fact")
    assert second["action"] == "created"

    new_detail = client.get(f"/api/v1/memories/{second['memory']['id']}", headers=headers).json()[
        "data"
    ]
    relations = new_detail["relations"]
    contradiction = next(
        (relation for relation in relations if relation["relation_type"] == "contradicts"),
        None,
    )
    assert contradiction is not None
    assert contradiction["target_id"] == first["memory"]["id"]

    old_detail = client.get(f"/api/v1/memories/{first['memory']['id']}", headers=headers).json()[
        "data"
    ]
    assert old_detail["status"] == "disputed"
    assert old_detail["contradiction_count"] == 1


def test_hard_delete_cascades_indexes_relations_and_evidence(client, app_context):
    _app, _config, paths = app_context
    headers = auth_headers()
    first = create_memory(client, headers, content="删除级联测试记忆甲", category="fact")
    second = create_memory(client, headers, content="删除级联测试记忆乙", category="fact")
    first_id = first["memory"]["id"]
    second_id = second["memory"]["id"]

    relation = client.post(
        "/api/v1/relations",
        headers=headers,
        json={
            "source_id": first_id,
            "target_id": second_id,
            "relation_type": "related_to",
            "strength": 0.8,
        },
    )
    assert relation.status_code == 200, relation.text
    assert client.get(f"/api/v1/memories/{first_id}", headers=headers).status_code == 200

    response = client.delete(f"/api/v1/memories/{first_id}", headers=headers)
    assert response.status_code == 200
    assert client.get(f"/api/v1/memories/{first_id}", headers=headers).status_code == 404

    connection = sqlite3.connect(paths.database_file)
    try:
        for table in (
            "memories",
            "memory_evidence",
            "memory_embeddings",
            "memory_relations",
            "memory_versions",
        ):
            if table == "memories":
                count = connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE id = ?", (first_id,)
                ).fetchone()[0]
            elif table == "memory_relations":
                count = connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE source_id = ? OR target_id = ?",
                    (first_id, first_id),
                ).fetchone()[0]
            else:
                count = connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE memory_id = ?", (first_id,)
                ).fetchone()[0]
            assert count == 0, table
        fts_count = connection.execute(
            "SELECT COUNT(*) FROM memory_fts WHERE memory_id = ?", (first_id,)
        ).fetchone()[0]
        assert fts_count == 0
    finally:
        connection.close()

    survivor = client.get(f"/api/v1/memories/{second_id}", headers=headers).json()["data"]
    assert survivor["id"] == second_id


def test_bulk_actions_archive_restore_and_delete(client):
    headers = auth_headers()
    ids = [
        create_memory(client, headers, content=f"批量操作记忆 {index}", category="fact")["memory"][
            "id"
        ]
        for index in range(3)
    ]

    response = client.post(
        "/api/v1/memories/bulk",
        headers=headers,
        json={"ids": ids, "action": "archive"},
    )
    assert response.status_code == 200
    assert len(response.json()["data"]["processed"]) == 3
    for memory_id in ids:
        detail = client.get(f"/api/v1/memories/{memory_id}", headers=headers).json()["data"]
        assert detail["status"] == "archived"

    client.post(
        "/api/v1/memories/bulk",
        headers=headers,
        json={"ids": ids, "action": "delete"},
    )
    for memory_id in ids:
        assert client.get(f"/api/v1/memories/{memory_id}", headers=headers).status_code == 404


def test_merge_preserves_source_as_superseded(client):
    headers = auth_headers()
    source = create_memory(client, headers, content="用户喜欢咖啡", category="preference")
    target = create_memory(client, headers, content="用户喜欢深烘焙咖啡", category="preference")
    response = client.post(
        f"/api/v1/memories/{target['memory']['id']}/merge",
        headers=headers,
        json={"source_id": source["memory"]["id"]},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["source"]["status"] == "superseded"
    assert data["target"]["content"] == "用户喜欢深烘焙咖啡"

    detail = client.get(f"/api/v1/memories/{target['memory']['id']}", headers=headers).json()[
        "data"
    ]
    assert any(
        item["attributes"].get("merged_from") == source["memory"]["id"]
        for item in detail["evidence"]
    )
    assert any(item["relation_type"] == "merged_into" for item in detail["relations"])


def test_rollback_restores_content_and_fts(client):
    headers = auth_headers()
    created = create_memory(client, headers, content="rollback oldtoken memory")
    memory_id = created["memory"]["id"]
    update = client.patch(
        f"/api/v1/memories/{memory_id}",
        headers=headers,
        json={"content": "rollback newtoken memory"},
    )
    assert update.status_code == 200

    response = client.post(
        f"/api/v1/memories/{memory_id}/rollback",
        headers=headers,
        json={"version": 1},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["content"] == "rollback oldtoken memory"

    old = client.post(
        "/api/v1/search",
        headers=headers,
        json={"query": "oldtoken", "mode": "manage"},
    ).json()["data"]["items"]
    assert old and old[0]["id"] == memory_id
    new = client.post(
        "/api/v1/search",
        headers=headers,
        json={"query": "newtoken", "mode": "manage"},
    ).json()["data"]["items"]
    assert new == []


def test_embedding_disabled_stores_memory_without_task(client, app_context):
    _app, config, _paths = app_context
    config.set("embedding.enabled", False)
    config.save()
    headers = auth_headers()
    created = create_memory(client, headers, content="离线模式下的记忆")
    assert created["embedding_pending"] is False

    tasks = client.get("/api/v1/tasks?status=completed", headers=headers).json()["data"]["items"]
    assert not any(task["task_type"] == "embed_memory" for task in tasks)
    detail = client.get(f"/api/v1/memories/{created['memory']['id']}", headers=headers).json()[
        "data"
    ]
    assert detail["id"] == created["memory"]["id"]
