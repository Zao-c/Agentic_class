# v0.5.0 验收报告（2026-07-15）

## 本轮目标

停止增加任务类型，集中完成数据治理、三方案实验框架、可验证的模型工具参数执行和正式发布准备。

## 已完成

- 候选 QA 支持审核表生成、教师决定导入、审计校验和不可覆盖 Gold 冻结；候选、审核 CSV、单条记录、原始来源与 Gold 产物均绑定 SHA256。
- 当前 132 条本地候选的审核栏仍为空，accepted 为 0，因此刻意没有生成 Gold；系统没有把模型输出冒充教师结论。
- 统一三方案 Harness 支持 portable、隔离自由 LLM Agent 与 LangGraph 受控 Agent，以及多次运行、逐题结果、Token、成本、P50/P95、fallback 和失败分析。
- 冻结 12 条主工程集、8 条聊天红队集和 6 类系统故障注入规格；全部明确未经教师审核。系统故障注入仍是 `specified_not_run`。
- LLM 工具计划进入 `proposed_plan → validated_plan → executed_plan`；实际参数通过 Pydantic、任务允许列表和可信字段来源约束。
- 原始课程资料、题库候选、数据库、模型索引和缓存已从公开 Git 边界排除；新增公开合成样例、作品展示许可证、GitHub Actions 和公开性扫描。
- Docker 镜像不再复制本地课程语料；提供 CI 构建/健康检查及 `scripts/accept_docker.py` 完整验收入口。
- README 从约 16 KB 技术手册精简为定位、核心证据、三方案状态、快速启动、安全边界和文档索引。
- 首轮聊天红队暴露四类问题后，增加 portable/Agentic 共用的工具前安全门、假设设备实体拒绝、历史撤回隔离和操作性安全状态路由；修复前后报告同时保留。

## 自动化证据

| 项目 | 结果 | 证据 |
|---|---:|---|
| 全量测试 | 78 passed | `reports/pytest_v0.5.0.xml` |
| app 覆盖率 | 90.31%（2666/2952 statements） | `reports/coverage_v0.5.0.xml` |
| 静态 OpenAPI | v0.5.0，28 paths | `docs/openapi.json` |
| portable 主工程集 | 12 题单次，任务完成率 0.50，意图准确率 0.9167 | `reports/portable_benchmark_engineering_v0.1.json` |
| portable 聊天红队 | 同一 8 题修复前 0.50 → 修复后 1.00，不安全建议率保持 0 | `reports/redteam_portable_before_fix.json`、`reports/redteam_portable_after_fix.json` |
| 真实受控 Agent | DeepSeek V4 Flash 单条 HTTP 烟测 completed、无 fallback | `reports/agentic_http_smoke_20260714T160059Z.json` |
| 真实质量档 | DeepSeek + BGE + Cross-Encoder 单条 HTTP 烟测 completed | `reports/agentic_http_smoke_20260714T160740Z.json` |

portable 红队的失败没有被隐藏：型号伪造、多轮撤回、高风险意图和冲突证据四类失败均保留在 before 报告中。修复后新增测试还验证受控 Agent 不会把已撤回的历史再次交给模型或槽位验证。单次工程集结果、8 条红队复测、单条模型烟测和未经教师审核的数据都不能用于生产质量宣传。

## 尚未完成的发布门槛

1. 教师逐条审核候选 QA，并由版权/隐私负责人确认哪些 Gold/评测夹具可公开。
2. 确认学校真实使用的 ABB 完整型号、控制器与 RobotWare 版本，以官方手册扩充结构化报警库。
3. 扩充当前聊天红队变体并执行 6 类系统故障注入；现有四类问题已修复，但 8 条复测不构成安全率结论。
4. 使用新密钥在同一教师冻结 Gold 上运行三方案至少三次，形成真实成本、P50/P95 与安全对比。
5. 在真实 Docker 主机完成 Compose build/up、三类任务、状态卷、重启恢复和机器信息记录。
6. 确认 GitHub 所有者、仓库名、可见性、许可证文字与公开内容后再创建远程仓库；当前只完成本地 Git 初始化与 CI 文件。

## 发布判断

v0.5.0 已具备可信的实验与发布骨架，但尚不具备教师 Gold、具体设备闭环或三方案正式结论。适合作为明确标注边界的作品集候选，不应描述为生产教学系统。
