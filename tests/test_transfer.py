from __future__ import annotations

import csv
import io
import json

from .conftest import auth_headers


def test_json_export_and_import_roundtrip(client):
    headers = auth_headers()
    created = client.post(
        "/api/v1/memories",
        headers=headers,
        json={
            "content": "导入导出的中文记忆",
            "category": "fact",
            "tags": ["导出"],
            "source_kind": "user_explicit",
        },
    ).json()["data"]["memory"]

    export = client.get("/api/v1/transfer/export?format=json", headers=headers)
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("application/json")
    document = json.loads(export.content)
    assert document["memories"][0]["content"] == "导入导出的中文记忆"

    assert client.delete(f"/api/v1/memories/{created['id']}", headers=headers).status_code == 200

    imported = client.post(
        "/api/v1/transfer/import",
        headers=headers,
        json={"format": "json", "payload": document},
    )
    assert imported.status_code == 200, imported.text
    result = imported.json()["data"]
    assert result["created"] == 1
    assert result["failed"] == 0

    listing = client.get("/api/v1/memories", headers=headers).json()["data"]
    assert any(item["content"] == "导入导出的中文记忆" for item in listing["items"])


def test_csv_export_and_import(client):
    headers = auth_headers()
    client.post(
        "/api/v1/memories",
        headers=headers,
        json={"content": "CSV 中文记忆一", "category": "fact"},
    )
    export = client.get("/api/v1/transfer/export?format=csv", headers=headers)
    assert export.status_code == 200
    assert export.content.startswith(b"\xef\xbb\xbf")
    text = export.content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows[0]["content"] == "CSV 中文记忆一"

    import_text = (
        "content,title,memory_type,category,source_kind,importance,confidence,tags\n"
        '"CSV 导入记忆","导入标题",long_term,fact,user_explicit,0.7,0.9,"[""csv""]"\n'
    )
    response = client.post(
        "/api/v1/transfer/import",
        headers=headers,
        json={"format": "csv", "payload": import_text},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["created"] == 1
    listing = client.get("/api/v1/memories?q=CSV%20%E5%AF%BC%E5%85%A5", headers=headers).json()[
        "data"
    ]
    assert listing["total"] == 1


def test_backup_create_and_restore(client, app_context):
    _app, _config, paths = app_context
    headers = auth_headers()
    first = client.post(
        "/api/v1/memories", headers=headers, json={"content": "备份前存在的记忆"}
    ).json()["data"]["memory"]
    response = client.post("/api/v1/backups", headers=headers)
    assert response.status_code == 200, response.text
    name = response.json()["data"]["name"]
    assert (paths.backup_dir / name).is_file()

    second = client.post(
        "/api/v1/memories", headers=headers, json={"content": "备份后新增的记忆"}
    ).json()["data"]["memory"]
    client.delete(f"/api/v1/memories/{first['id']}", headers=headers)

    archive = (paths.backup_dir / name).read_bytes()
    restore = client.post(
        "/api/v1/backups/restore",
        headers=headers,
        files={"file": (name, archive, "application/zip")},
    )
    assert restore.status_code == 200, restore.text
    assert restore.json()["data"]["restored"] is True

    assert client.get(f"/api/v1/memories/{first['id']}", headers=headers).status_code == 200
    assert client.get(f"/api/v1/memories/{second['id']}", headers=headers).status_code == 404


def test_config_update_encrypts_api_key(client, app_context):
    _app, config, _paths = app_context
    headers = auth_headers()
    response = client.patch(
        "/api/v1/config",
        headers=headers,
        json={
            "embedding": {
                "provider": "openai_compatible",
                "model": "text-embedding-3-small",
                "base_url": "http://127.0.0.1:9/v1",
                "api_key": "sk-super-secret",
                "dimensions": 1536,
            }
        },
    )
    assert response.status_code == 200, response.text
    public = response.json()["data"]
    assert public["embedding"]["api_key_set"] is True
    assert "sk-super-secret" not in json.dumps(public)
    assert "sk-super-secret" not in _paths.config_file.read_text(encoding="utf-8")
    assert config.get("embedding.api_key_encrypted")


def test_embedding_test_endpoint_with_hashing(client):
    response = client.post(
        "/api/v1/config/embedding/test",
        headers=auth_headers(),
        json={"provider": "hashing", "model": "local-hash-512", "dimensions": 512},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["ok"] is True
    assert data["dimensions"] == 512
