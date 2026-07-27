---
kind: external_dependency
name: MinIO 对象存储服务
slug: minio
category: external_dependency
category_hints:
    - vendor_identity
    - client_constraint
scope:
    - '**'
---

### MinIO 对象存储服务
- **角色**：作业文件、图片、PDF报告、音频等二进制文件的持久化存储
- **集成点**：`MINIO_ENDPOINT`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、`MINIO_BUCKET`、`MINIO_PUBLIC_ENDPOINT` 配置；开发模式下回退到 `LOCAL_STORAGE_DIR=./uploads`
- **使用模式**：通过 minio SDK 客户端操作，生产环境部署独立 MinIO 实例，开发环境可用 Docker 启动
- **约束**：本地存储仅用于 DEV_MODE=true，生产必须配置 MinIO；文件访问 URL 根据环境动态生成