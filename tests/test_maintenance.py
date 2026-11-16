from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from .conftest import auth_headers


def _create(client, headers, content, **extra):
    response = client.post("/api/v1/memories", headers=headers, json={"content": content, **extra})
    assert response.status_code == 200, response.text
    return response.json()["data"]["memory"]


def test_consolidation_creates_summary_without_overwriting_originals(client):
    headers = auth_headers()
    originals = [
        _create(
            client,
            headers,
            "今天去西湖边散步，看到了盛开的荷花。",
            memory_type="episodic",
            category="event",
        ),
        _create(
            client,
            headers,
            "晚上和小李一起吃火锅，聊了很久的工作。",
            memory_type="episodic",
            category="event",
        ),
        _create(
            client,
            headers,
            "下午在图书馆读完了人类简史的最后一章。",
            memory_type="episodic",
            category="event",
        ),
    ]

    response = client.post("/api/v1/maintenance/consolidate", headers=headers, json={})
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["summaries"] == 1, result

    listing = client.get("/api/v1/memories?category=summary", headers=headers).json()["data"]
    assert listing["total"] == 1
    summary = listing["items"][0]
    assert summary["source_kind"] == "model_summary"
    assert summary["memory_type"] == "semantic"

    detail = client.get(f"/api/v1/memories/{summary['id']}", headers=headers).json()["data"]
    assert detail["memory_type"] == "semantic"
    assert len(detail["evidence"]) >= 3
    assert {item["relation_type"] for item in detail["relations"]} >= {"consolidated_from"}

    for original in originals:
        original_detail = client.get(f"/api/v1/memories/{original['id']}", headers=headers).json()[
            "data"
        ]
        assert original_detail["content"] == original["content"]
        assert original_detail["status"] == "active"
        assert original_detail["attributes"].get("consolidated_into") == summary["id"]

    stats = client.get("/api/v1/stats", headers=headers).json()["data"]
    assert stats["pending_consolidation"] == 0


def test_decay_soft_forgets_low_value_memory(client, app_context):
    _app, _config, paths = app_context
    headers = auth_headers()
    memory = _create(client, headers, "一条很久没有被访问的低价值记忆")
    old = (datetime.utcnow() - timedelta(days=500)).strftime("%Y-%m-%d %H:%M:%S.%f")
    connection = sqlite3.connect(paths.database_file)
    try:
        connection.execute(
            "UPDATE memories SET created_at=?, updated_at=?, last_reinforced_at=?, "
            "last_accessed_at=NULL, importance=0.1, access_count=0, retention=1.0 "
            "WHERE id=?",
            (old, old, old, memory["id"]),
        )
        connection.commit()
    finally:
        connection.close()

    response = client.post("/api/v1/maintenance/decay", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["forgotten"] == 1

    detail = client.get(f"/api/v1/memories/{memory['id']}", headers=headers).json()["data"]
    assert detail["status"] == "archived"
    events = client.get(f"/api/v1/events?memory_id={memory['id']}", headers=headers).json()["data"][
        "items"
    ]
    assert any(event["event_type"] == "forgotten_soft" for event in events)


def test_rebuild_index_repairs_fts(client):
    headers = auth_headers()
    memory = _create(client, headers, "索引重建测试内容")
    response = client.post("/api/v1/maintenance/rebuild-index", headers=headers, json={})
    assert response.status_code == 200
    assert response.json()["data"]["fts_indexed"] == 1

    hits = client.post(
        "/api/v1/search",
        headers=headers,
        json={"query": "索引重建", "mode": "manage"},
    ).json()["data"]["items"]
    assert hits and hits[0]["id"] == memory["id"]


def test_task_listing_and_retry(client):
    headers = auth_headers()
    response = client.get("/api/v1/tasks", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["total"] >= 0
