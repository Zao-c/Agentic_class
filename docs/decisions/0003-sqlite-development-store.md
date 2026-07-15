# ADR-0003：开发阶段使用 SQLite

- 状态：已采纳
- 日期：2026-07-14

## 决策

Run、Event、Feedback、bad case、学习记录、文档和 Chunk 在开发阶段使用同一个 SQLite 数据库，并启用 WAL 和外键。

## 原因

它让新环境和 Docker Compose 无需额外服务即可运行，同时保留清晰 Repository 边界。生产并发和权限要求明确后，可迁移 PostgreSQL；Trace JSON Schema 不随存储更换。
