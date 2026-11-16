# Embedding、向量索引与混合检索

## 1. 两种 Embedding Provider

### 1.1 Hashing（默认，离线）

配置：

```json
{
  "embedding": {
    "enabled": true,
    "provider": "hashing",
    "model": "local-hash-512",
    "dimensions": 512
  }
}
```

- 对英文词、CJK 单字与 bigram 做 BLAKE2b 哈希，映射到固定维度，带符号累加后 L2 归一化。
- 确定性、零依赖、无网络，适合首次启动、离线环境和自动化测试。
- 它提供的是词法相似度，不是深度语义；对同义改写不如真实 embedding。

### 1.2 OpenAI-compatible

支持 OpenAI、Azure-compatible 网关、Ollama、LM Studio、vLLM、DeepSeek、SiliconFlow、OpenRouter 等任何实现 `POST /embeddings` 的服务：

```json
{
  "embedding": {
    "enabled": true,
    "provider": "openai_compatible",
    "model": "text-embedding-3-small",
    "base_url": "https://api.openai.com/v1",
    "dimensions": 1536,
    "batch_size": 32,
    "timeout_seconds": 30,
    "normalize": true,
    "api_key_encrypted": "gAAAAA..."
  }
}
```

推荐通过 API 写入，而不是手工编辑加密字段：

```bash
curl -X PATCH http://127.0.0.1:6190/api/v1/config \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Mori-Role: admin" \
  -H "Content-Type: application/json" \
  -d '{
    "embedding": {
      "provider": "openai_compatible",
      "model": "text-embedding-3-small",
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-...",
      "dimensions": 1536
    }
  }'
```

- `api_key` 只在请求中出现，服务端用安装密钥派生 Fernet 加密后保存。
- 读取配置永远不返回明文，只返回 `api_key_set`。
- 修改 Provider/模型后，已有向量不会自动匹配新模型。请调用
  `POST /api/v1/maintenance/reembed`，并等待任务完成。
- `MORIMEMORY_EMBEDDING_API_KEY`、`MORIMEMORY_EMBEDDING_BASE_URL`、
  `MORIMEMORY_EMBEDDING_MODEL` 可覆盖配置文件，适合容器/密钥管理。

### 1.3 连接测试

```http
POST /api/v1/config/embedding/test
```

```json
{
  "provider": "openai_compatible",
  "model": "text-embedding-3-small",
  "base_url": "http://127.0.0.1:11434/v1",
  "api_key": "",
  "dimensions": 1536
}
```

返回实际维度、耗时和错误信息。测试不会持久化；保存配置后再次测试当前配置。

## 2. 向量存储

### 2.1 SQLite 后端（默认）

`memory_embeddings` 每个 `(memory_id, provider, model)` 保存：

- `vector`：packed float32 BLOB；
- `norm`：写入时 L2 范数；
- `dimensions`；
- `content_hash`：与记忆内容对应，内容变化时更新。

检索时对可访问空间内的向量做精确余弦扫描。数千到数万条记忆在本地毫秒级完成。

### 2.2 替换后端

实现 `morimemory.vector_store.VectorIndex`：

```python
from morimemory.vector_store import VectorIndex, register_vector_backend

class MyVectorIndex(VectorIndex):
    name = "my-backend"

    async def search(self, session, repository, *, provider, model, space_ids,
                     statuses, query_vector, limit):
        # 返回 [(memory_id, cosine_score), ...]
        ...

register_vector_backend("my-backend", MyVectorIndex)
```

然后在配置中：

```json
{ "vector": { "backend": "my-backend" } }
```

检索管线、权限过滤、打分和 Token 预算不需要修改。默认还有 `none` 后端可用于禁用向量召回。

## 3. SQLite FTS5 与 CJK

SQLite 的 `unicode61` 不会切分中文。MoriMemory 在写入 FTS 前把文本转换为：

- 英文/数字词；
- CJK 单字；
- CJK bigram。

例如“我喜欢喝绿茶”索引为 `我 喜 欢 喝 绿 茶 我喜 喜欢 欢喝 喝绿 绿茶`。

查询同样转换：

- 检索接口使用 OR 提高召回；
- 表格关键词筛选使用 AND 提高精确度；
- 英文 token 自动加前缀 `*`，支持部分单词匹配。

如果当前 Python 的 SQLite 不含 FTS5，服务自动回退到 `LIKE`，健康检查会显示
`fts5=false`。建议使用官方 Python 3.12+ 构建。

## 4. 混合打分

```text
score =
  w_semantic  × max(0, cosine)
+ w_keyword   × normalized_bm25
+ w_importance× importance
+ w_confidence× confidence
+ w_recency   × exp(-ln2 × age_days / half_life)
+ w_strength  × retention × (0.7 + 0.3 × log1p(reinforcements)/log1p(10))
+ relation_boost
+ pinned_boost
```

默认权重：

```json
{
  "semantic": 0.35,
  "keyword": 0.20,
  "importance": 0.15,
  "confidence": 0.10,
  "recency": 0.10,
  "strength": 0.10
}
```

实现会归一化权重总和，允许用户调整。`relation_boost` 来自一跳关系扩展，最多约 0.12；置顶额外 +0.05。

查询相关性门控：如果查询非空，候选必须满足关键词命中或语义相似度不低于
`retrieval.min_semantic_similarity`（默认 0.12），避免仅凭重要性把无关记忆注入上下文。

## 5. Token 预算

`estimate_tokens` 按字符估算：

- CJK 字符约 1 token；
- 非 CJK 字符约 4 字符 / token；
- 每条记忆额外加 8 token 开销。

`/context` 按分数排序累加，超出 `token_budget` 时停止并返回 `truncated=true`；至少保留第一条，避免预算极小时返回空上下文。

## 6. 上下文格式

```text
以下是从 MoriMemory 检索到的长期记忆，仅作为背景信息使用。
带“AI 推断 / 模型归纳 / 行为观察”标记的内容不是用户明确陈述，不得当作事实复述。

- [用户明确表达 · preference] 用户喜欢手冲咖啡，不加糖。
- [AI 推断 · identity] 森认为用户是一个对细节敏感的人。
```

MoriBot 应把这段文本放入 system 或独立的 memory 段落，并保留来源标签；不要在提示词中去掉标签。

## 7. 性能建议

- 记忆量 < 5 万：SQLite 向量扫描足够；优先优化 FTS 与筛选。
- 记忆量 > 5 万：接入外部向量后端；或先按空间/状态预过滤再扫描。
- embedding 批量写入使用 `batch_size=32`，避免单条 HTTP 往返。
- 修改 embedding 模型后必须重建；不同模型的向量不能混用。
- 定期调用 `POST /maintenance/rebuild-index` 修复 FTS（例如数据库异常恢复后）。
