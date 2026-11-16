# 数据迁移、备份与恢复

## 1. 数据目录

| 路径 | 说明 |
| --- | --- |
| `data/config.json` | 运行配置、服务令牌、加密后的 API Key |
| `data/morimemory.db` | SQLite 主库（WAL 模式） |
| `data/morimemory.db-wal` / `-shm` | SQLite WAL 文件，运行中自动维护 |
| `data/logs/morimemory.log` | 滚动日志（10 MB × 14 天） |
| `data/backups/*.zip` | 手动/调度备份 |
| `data/memory-service.lock` | 嵌入式模式进程锁 |
| `data/service-token` | 嵌入式模式服务令牌（0600） |

MoriBot 集成时把数据目录设为 `MoriBot/data/memory`，因此 MoriBot 现有的
`data/` ZIP 备份会自动包含记忆库。注意：MoriBot 备份的是运行中的数据库文件；
MoriMemory 自己的备份使用 SQLite 在线备份 API，额外保证 WAL 一致性。

## 2. Schema 迁移

当前 schema 版本：`1`。

- `schema_migrations` 记录已应用版本、描述和时间。
- 启动时 `Database.initialize()` 执行：
  1. `Base.metadata.create_all`（新库建表）；
  2. FTS5 可用性探测与虚拟表创建；
  3. 读取 `schema_migrations`；
  4. 依次执行 `MIGRATIONS` 中尚未应用的版本；
  5. 如果数据库版本高于当前程序，记录警告但继续运行。
- 新增迁移：

```python
# morimemory/db/migrations.py
async def migration_2_add_field(connection):
    await connection.execute(text("ALTER TABLE memories ADD COLUMN foo TEXT"))

MIGRATIONS = [
    (2, "add foo", migration_2_add_field),
]
LATEST_SCHEMA_VERSION = 2
```

迁移必须是前向、幂等、可在失败时回滚事务。涉及 FTS/向量索引的变更需要同时重建索引。

## 3. 创建备份

API：

```http
POST /api/v1/backups
```

CLI：

```bash
uv run morimemory backup
```

流程：

1. `PRAGMA wal_checkpoint(TRUNCATE)`；
2. `sqlite3.Connection.backup()` 生成一致性快照；
3. ZIP 写入 `manifest.json`、`morimemory.db`、`config.json`；
4. manifest 记录每个文件的 SHA-256、大小、schema 版本、创建时间；
5. 按 `backup.max_files` 清理旧备份。

文件名：`morimemory-backup-YYYYmmdd-HHMMSS.zip`。

## 4. 恢复备份

```http
POST /api/v1/backups/restore
Content-Type: multipart/form-data
file=@morimemory-backup-20261113-120000.zip
```

校验：

- ZIP 可读；
- `manifest.json` 格式为 `morimemory-backup`；
- 文件名不含绝对路径/`..`；
- 数据库 SHA-256 与 manifest 一致；
- `PRAGMA integrity_check` 返回 `ok`；
- 上限 1 GB。

恢复过程：

1. 复制当前数据库到 `morimemory.pre-restore-YYYYmmdd-HHMMSS.db`；
2. 释放 SQLAlchemy 连接池；
3. 删除 `-wal` / `-shm`；
4. 原子替换数据库；
5. 重新初始化 schema；
6. **保留当前 `config.json` 和令牌**，避免旧备份中的令牌使客户端失联。

恢复过程中应避免并发写入（维护窗口或无人使用时段）。

## 5. 导出与导入

### 5.1 JSON

```http
GET /api/v1/transfer/export?format=json
```

```json
{
  "format": "morimemory-memories",
  "version": 1,
  "exported_at": "...",
  "spaces": [],
  "memories": [
    { "content": "...", "evidence": [], "...": "..." }
  ],
  "relations": []
}
```

### 5.2 CSV

```http
GET /api/v1/transfer/export?format=csv
```

UTF-8 BOM，便于 Excel 打开。标签和 attributes 为 JSON 字符串。

### 5.3 导入

```http
POST /api/v1/transfer/import
Content-Type: application/json

{
  "format": "json",
  "payload": { "memories": [] },
  "target_space_id": null,
  "deduplicate": true
}
```

- 支持完整导出文档、记忆数组、CSV 文本。
- 原 `space_id` 在当前库中存在且可访问时复用，否则按 `scope` 回退到用户空间。
- 导入默认把来源标记为 `ai_inference`，并加 `attributes.imported=true`；如果文件明确声明
  `source_kind=user_explicit`，则保留。
- 导入不保证保留原 ID 和关系图；需要完整保真迁移时使用备份/恢复，而不是 JSON 导入。
- 返回 `created`、`reinforced`、`failed`、`errors`。

## 6. 从 MoriBot 迁移

1. 在旧机器执行 MoriBot 的系统备份（包含 `data/`）。
2. 在新机器恢复 MoriBot 备份，确保 `data/memory/` 存在。
3. 若使用默认配置，MoriBot 启动时自动发现 `data/memory` 并启动服务。
4. 如果更换了 embedding 模型，调用 `/maintenance/reembed`。
5. 检查 `/api/v1/health` 的 `schema_version`、`fts5`、`database.ok`。

## 7. 灾难恢复

| 场景 | 处理 |
| --- | --- |
| 数据库损坏 | 用 `sqlite3 morimemory.db "PRAGMA integrity_check"` 确认；从最近 ZIP 恢复 |
| FTS 索引损坏 | `POST /maintenance/rebuild-index` |
| 向量维度不匹配 | 确认 embedding 配置；`POST /maintenance/reembed` |
| 误删记忆 | 仅能从事先备份恢复；硬删除不可撤销，软归档可恢复 |
| 服务令牌丢失 | 查看 `data/config.json` 的 `security.token`；嵌入式模式查看 `data/memory/service-token` |
| 配置损坏 | 启动会备份为 `config.broken-*.json` 并重建默认值 |

## 8. 版本升级

1. 停止 MoriBot/MoriMemory。
2. 备份 `data/`。
3. 更新 MoriMemory 代码并 `uv sync`。
4. 执行 `uv run morimemory migrate`。
5. 启动服务，查看 `/health` 与 `/api/v1/stats`。
6. 如涉及 embedding 变更，执行 reembed 并等待任务完成。

迁移是前向的；降级前必须恢复旧版本对应的备份。
