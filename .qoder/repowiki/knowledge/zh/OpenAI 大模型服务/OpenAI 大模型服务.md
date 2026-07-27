---
kind: external_dependency
name: OpenAI 大模型服务
slug: openai
category: external_dependency
category_hints:
    - vendor_identity
    - auth_protocol
scope:
    - '**'
---

### OpenAI 大模型服务
- **角色**：提供多模态推理（题目识别、评分、知识点提取）与文本嵌入（向量化）能力
- **集成点**：`LLM_API_KEY`、`LLM_API_BASE`（默认 `https://api.openai.com/v1`）、`LLM_MODEL`（默认 gpt-4o）、`EMBEDDING_MODEL`（默认 text-embedding-3-small）通过环境变量注入
- **使用模式**：后端通过 openai SDK 调用，支持 Function Calling 的 Agent 工具编排；向量检索使用独立的 embedding 模型
- **注意**：生产环境需配置真实 API Key，开发模式可留空或指向兼容接口