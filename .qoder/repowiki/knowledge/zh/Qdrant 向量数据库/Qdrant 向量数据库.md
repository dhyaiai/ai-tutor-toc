---
kind: external_dependency
name: Qdrant 向量数据库
slug: qdrant
category: external_dependency
category_hints:
    - vendor_identity
    - migration_status
scope:
    - '**'
---

### Qdrant 向量数据库
- **角色**：作业分析文本、题目内容的向量存储与语义检索，支撑 RAG 问答与智能搜索
- **集成点**：`QDRANT_HOST`、`QDRANT_PORT` 配置；文档中同时提及 Milvus，当前实现以 Qdrant 为准
- **迁移状态**：项目从 Milvus 迁移至 Qdrant，代码依赖 qdrant-client，但文档仍保留 Milvus 引用需同步更新
- **使用模式**：异步客户端连接，Collection 名为 `analysis_chunks`，按 assignment_id、subject 等维度组织元数据