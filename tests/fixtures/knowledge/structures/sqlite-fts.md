# SQLite FTS5 全文检索实践

SQLite FTS5 是 SQLite 内置的全文检索扩展，支持 `bm25` 排序与多种 tokenizer。本文整理中文场景下的实践要点。

## 建表与基本查询

使用 `CREATE VIRTUAL TABLE` 创建 FTS5 虚拟表，列默认被索引。

```sql
CREATE VIRTUAL TABLE knowledge_evidence_fts USING fts5(
    source_title,
    heading_path,
    content,
    tokenize = 'trigram'
);
```

查询使用 `MATCH` 操作符，配合 `bm25` 函数按相关性排序：

```sql
SELECT evidence_id, bm25(knowledge_evidence_fts) AS score
FROM knowledge_evidence_fts
WHERE knowledge_evidence_fts MATCH 'ISR OR 同步'
ORDER BY score;
```

> `bm25` 返回负值，越小（绝对值越大）越相关，因此 `ORDER BY score` 即可让最相关结果在前。

## trigram tokenizer 与中文

`trigram` tokenizer 按 3 字符滑动窗口切分，适合没有空格分词的中文。

trigram 行为：

1. 输入至少 3 字符才能产生 token。
2. "卡夫卡" 产生一个完整 trigram。
3. "卡夫"（2 字）不会产生 trigram，需走 LIKE 回退。

## 加权与分列排序

FTS5 支持按列加权。`bm25(table, w0, w1, w2)` 为每列指定权重。

| 列 | 典型权重 | 含义 |
|---|---|---|
| source_title | 8.0 | 标题命中权重最高 |
| heading_path | 4.0 | 标题路径中等 |
| content | 1.0 | 正文基础权重 |

## 短查询回退

少于 3 字符的查询（如 ASCII "AI"、单字 CJK）无法产生 trigram，应走有上限的 LIKE 子串回退，避免全库无界扫描。

```python
if len(query) < 3:
    stmt = "SELECT ... WHERE content LIKE :pattern LIMIT 50"
```

## 错误处理

FTS5 语法错误（如未闭合引号）会抛 `OperationalError`。Spec 要求显式抛出稳定错误码，禁止 `except: return []` 静默吞掉变成空结果。
