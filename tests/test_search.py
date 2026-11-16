from __future__ import annotations

import json

from .conftest import auth_headers


def test_ingest_extracts_atomic_claims_with_evidence(client):
    headers = auth_headers(role="service")
    response = client.post(
        "/api/v1/conversations/ingest",
        headers=headers,
        json={
            "messages": [
                {
                    "id": "m1",
                    "role": "user",
                    "content": "我叫小明，我喜欢喝绿茶，我住在上海。",
                },
                {
                    "id": "m2",
                    "role": "assistant",
                    "content": "很高兴认识你，我是你的助手。",
                },
            ],
            "async_mode": False,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["candidates"] == 3
    assert data["created"] == 3
    assert data["spaces"]["user"]
    assert data["spaces"]["character"]
    assert data["spaces"]["conversation"]

    listing = client.get("/api/v1/memories", headers=auth_headers(role="admin")).json()["data"]
    assert listing["total"] == 3
    by_category = {item["category"]: item for item in listing["items"]}
    assert by_category["identity"]["content"] == "我叫小明"
    assert by_category["preference"]["content"] == "我喜欢喝绿茶"
    assert by_category["fact"]["content"] == "我住在上海"
    for item in listing["items"]:
        assert item["source_kind"] == "user_explicit"
        # Character conversations keep durable facts inside the character
        # space; nothing is written to the shared user space.
        assert item["space"]["kind"] == "character"
        assert item["space"]["character_id"] == "char1"

    detail = client.get(
        f"/api/v1/memories/{by_category['preference']['id']}",
        headers=auth_headers(role="admin"),
    ).json()["data"]
    assert detail["evidence"][0]["message_id"] == "m1"
    assert detail["evidence"][0]["conversation_id"] == "conv1"
    assert detail["evidence"][0]["quote"] == "我喜欢喝绿茶"

    entities = client.get("/api/v1/entities", headers=auth_headers(role="admin")).json()["data"][
        "items"
    ]
    names = {entity["name"] for entity in entities}
    assert "小明" in names
    assert "上海" in names


def test_cjk_keyword_and_hybrid_search(client):
    headers = auth_headers()
    for content in ["我喜欢喝绿茶", "我养了一只橘猫", "我每天跑步五公里"]:
        client.post("/api/v1/memories", headers=headers, json={"content": content})

    response = client.post(
        "/api/v1/search",
        headers=headers,
        json={"query": "绿茶", "mode": "manage", "limit": 5},
    )
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["content"] == "我喜欢喝绿茶"
    assert items[0]["scores"]["keyword"] > 0
    assert items[0]["reasons"]

    partial = client.post(
        "/api/v1/search",
        headers=headers,
        json={"query": "橘猫", "mode": "manage", "limit": 5},
    ).json()["data"]["items"]
    assert partial and partial[0]["content"] == "我养了一只橘猫"


def test_context_assembly_and_token_budget(client):
    headers = auth_headers()
    for index in range(6):
        client.post(
            "/api/v1/memories",
            headers=headers,
            json={
                "content": f"关于项目 MoriMemory 的记忆片段 {index}：" + "很长的细节" * 30,
                "category": "fact",
            },
        )

    response = client.post(
        "/api/v1/context",
        headers=headers,
        json={
            "query": "MoriMemory",
            "mode": "manage",
            "limit": 6,
            "token_budget": 64,
            "record_access": True,
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["context"]
    assert data["truncated"] is True
    assert data["tokens_used"] >= 1
    assert "AI 推断" in data["context"]
    assert data["items"]

    accessed = client.get(f"/api/v1/memories/{data['items'][0]['id']}", headers=headers).json()[
        "data"
    ]
    assert accessed["access_count"] >= 1
    assert accessed["last_accessed_at"] is not None


def test_inference_is_not_presented_as_user_fact(client):
    headers = auth_headers()
    response = client.post(
        "/api/v1/memories",
        headers=headers,
        json={
            "content": "用户可能不喜欢嘈杂的环境",
            "source_kind": "ai_inference",
            "confidence": 0.99,
        },
    )
    assert response.status_code == 200
    memory = response.json()["data"]["memory"]
    assert memory["source_kind"] == "ai_inference"
    assert memory["confidence"] <= 0.7

    context = client.post(
        "/api/v1/context",
        headers=headers,
        json={"query": "嘈杂", "mode": "manage"},
    ).json()["data"]
    assert "AI 推断" in context["context"]
    assert "用户可能不喜欢嘈杂的环境" in context["context"]


def test_retrieval_is_space_scoped_and_supports_character_isolation(client):
    admin = auth_headers(role="admin")
    char1 = auth_headers(character_id="char1", role="service")
    char2 = auth_headers(character_id="char2", role="service")

    response = client.post(
        "/api/v1/memories",
        headers=admin,
        json={
            "content": "角色一号的私密设定：喜欢雨天",
            "scope": "character",
            "category": "preference",
            "source_kind": "ai_inference",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["memory"]["space"]["character_id"] == "char1"

    hit1 = client.post(
        "/api/v1/search",
        headers=char1,
        json={"query": "雨天", "mode": "recall"},
    ).json()["data"]["items"]
    assert any("角色一号" in item["content"] for item in hit1)

    hit2 = client.post(
        "/api/v1/search",
        headers=char2,
        json={"query": "雨天", "mode": "recall"},
    ).json()["data"]["items"]
    assert not any("角色一号" in item["content"] for item in hit2)

    assert (
        client.get(
            f"/api/v1/memories/{response.json()['data']['memory']['id']}", headers=char2
        ).status_code
        == 404
    )


def test_user_memory_grant_can_be_revoked(client):
    admin = auth_headers(role="admin")
    char2 = auth_headers(character_id="char2", role="service")

    created = client.post(
        "/api/v1/memories",
        headers=admin,
        json={"content": "用户最喜欢的城市是杭州", "category": "preference"},
    ).json()["data"]["memory"]
    user_space_id = created["space_id"]

    before = client.post(
        "/api/v1/search",
        headers=char2,
        json={"query": "杭州", "mode": "recall"},
    ).json()["data"]["items"]
    assert before == []

    grant = client.post(
        "/api/v1/grants",
        headers=admin,
        json={
            "space_id": user_space_id,
            "grantee_id": "char2",
            "permission": "read",
        },
    )
    assert grant.status_code == 200, grant.text
    grant_id = grant.json()["data"]["grant"]["id"]

    after = client.post(
        "/api/v1/search",
        headers=char2,
        json={"query": "杭州", "mode": "recall"},
    ).json()["data"]["items"]
    assert any("杭州" in item["content"] for item in after)

    revoked = client.delete(f"/api/v1/grants/{grant_id}", headers=admin)
    assert revoked.status_code == 200
    after_revoke = client.post(
        "/api/v1/search",
        headers=char2,
        json={"query": "杭州", "mode": "recall"},
    ).json()["data"]["items"]
    assert after_revoke == []


def test_ingested_character_memories_are_private_per_character(client):
    char1 = auth_headers(
        character_id="char1",
        conversation_id="conv-a",
        role="service",
    )
    char2 = auth_headers(
        character_id="char2",
        conversation_id="conv-b",
        role="service",
    )
    response = client.post(
        "/api/v1/conversations/ingest",
        headers=char1,
        json={
            "character_name": "Mori",
            "messages": [
                {
                    "id": "private-1",
                    "role": "user",
                    "content": "我叫小明，我喜欢喝绿茶，我住在上海。",
                }
            ],
            "async_mode": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["created"] == 3

    listing = client.get(
        "/api/v1/memories",
        headers=auth_headers(role="admin"),
    ).json()["data"]
    assert listing["total"] == 3
    assert {item["space"]["character_id"] for item in listing["items"]} == {"char1"}
    assert all(item["space"]["kind"] == "character" for item in listing["items"])

    char1_hits = client.post(
        "/api/v1/context",
        headers=char1,
        json={"query": "绿茶", "mode": "recall"},
    ).json()["data"]
    assert "绿茶" in char1_hits["context"]

    char2_hits = client.post(
        "/api/v1/context",
        headers=char2,
        json={"query": "绿茶", "mode": "recall"},
    ).json()["data"]
    assert char2_hits["context"] == ""
    assert char2_hits["items"] == []

    spaces = client.get(
        "/api/v1/spaces?kind=user",
        headers=auth_headers(role="admin"),
    ).json()["data"]
    user_space = next(
        space
        for space in spaces["items"]
        if space["kind"] == "user" and space["user_id"] == "u1"
    )
    assert user_space["memory_count"] == 0


def test_conversation_memories_stay_with_one_character(client):
    char1_a = auth_headers(
        character_id="char1",
        conversation_id="conv-a",
        role="service",
    )
    char1_b = auth_headers(
        character_id="char1",
        conversation_id="conv-b",
        role="service",
    )
    char2_b = auth_headers(
        character_id="char2",
        conversation_id="conv-b",
        role="service",
    )
    response = client.post(
        "/api/v1/conversations/ingest",
        headers=char1_a,
        json={
            "messages": [
                {
                    "id": "event-1",
                    "role": "user",
                    "content": "今天去了西湖，湖边的荷花开得很好。",
                }
            ],
            "async_mode": False,
        },
    )
    assert response.status_code == 200, response.text

    same_character = client.post(
        "/api/v1/context",
        headers=char1_b,
        json={"query": "西湖", "mode": "recall"},
    ).json()["data"]
    assert "西湖" in same_character["context"]

    other_character = client.post(
        "/api/v1/context",
        headers=char2_b,
        json={"query": "西湖", "mode": "recall"},
    ).json()["data"]
    assert other_character["context"] == ""
    assert other_character["items"] == []


def test_list_focus_id_returns_page_containing_memory(client):
    headers = auth_headers()
    ids = []
    for index in range(30):
        ids.append(
            client.post(
                "/api/v1/memories",
                headers=headers,
                json={"content": f"定位测试记忆 {index:02d}", "importance": index / 30},
            ).json()["data"]["memory"]["id"]
        )
    target = ids[7]
    response = client.get(
        f"/api/v1/memories?focus_id={target}&per_page=5&sort_by=created_at&sort_order=asc",
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["focus_id"] == target
    assert any(item["id"] == target for item in data["items"])
    assert data["page"] == 2


def test_export_is_utf8_and_scoped(client):
    headers = auth_headers()
    client.post("/api/v1/memories", headers=headers, json={"content": "中文导出测试"})
    response = client.get("/api/v1/transfer/export?format=json", headers=headers)
    assert response.status_code == 200
    document = json.loads(response.content)
    assert document["format"] == "morimemory-memories"
    assert document["memories"][0]["content"] == "中文导出测试"


def test_relation_expansion_adds_context_neighbors(client):
    headers = auth_headers()
    anchor = client.post(
        "/api/v1/memories",
        headers=headers,
        json={"content": "西湖边的荷花开了", "category": "event", "memory_type": "episodic"},
    ).json()["data"]["memory"]
    neighbor = client.post(
        "/api/v1/memories",
        headers=headers,
        json={"content": "橘猫团子今天很安静", "category": "fact"},
    ).json()["data"]["memory"]
    relation = client.post(
        "/api/v1/relations",
        headers=headers,
        json={
            "source_id": anchor["id"],
            "target_id": neighbor["id"],
            "relation_type": "related_to",
            "strength": 0.9,
        },
    )
    assert relation.status_code == 200

    hits = client.post(
        "/api/v1/search",
        headers=headers,
        json={"query": "荷花", "mode": "manage", "limit": 5, "expand_relations": True},
    ).json()["data"]["items"]
    ids = {item["id"] for item in hits}
    assert anchor["id"] in ids
    assert neighbor["id"] in ids
    neighbor_hit = next(item for item in hits if item["id"] == neighbor["id"])
    assert "related" in neighbor_hit["reasons"]
