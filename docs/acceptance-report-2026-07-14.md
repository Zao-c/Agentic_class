# 阶段 5 与项目交付验收报告

- 验收日期：2026-07-14
- 发布版本：0.3.0
- 环境：Windows，Python 3.11.15，Conda `rag-agent`
- 默认档位：`portable / hybrid_rerank`

## 结论

阶段 5 的 README、快速启动、静态 OpenAPI、架构/状态/数据流图、运行截图、Benchmark、典型 bad case、设计决策和可复现演示脚本已经齐全。三类任务及证据拒答通过真实 HTTP 演示；自动化测试、检索评测、诊断评测、辅导评测和 bad case 回归均有原始机器报告。

唯一未在本机关闭的最终验收项是 Docker 运行态构建：已修复镜像漏带 `data/structured` 的缺陷并加入静态防回归测试，但当前机器执行 `docker version` 返回“命令不存在”，因此没有宣称容器构建通过。

## 验收矩阵

| 验收项 | 状态 | 证据 |
|---|---|---|
| README 与快速启动 | 通过 | `README.md`、`docs/quickstart.md` |
| 三类任务独立路由 | 通过 | `reports/demo_20260714T140218Z.json` |
| 故障追问、工具、安全与转交 | 通过 | 7 条诊断评测和真实演示 4 工具链 |
| 回答引用与证据不足拒答 | 通过 | 真实演示：问答 5 条引用、域外问题 `abstained` |
| Trace 查询与导出 | 通过 | 演示报告记录各场景 Trace 事件与工具；Schema 位于 `docs/trace-schema.json` |
| 固定评测一键运行 | 通过 | 检索、诊断、辅导脚本均生成 JSON/Markdown |
| 自动化测试 | 通过 | JUnit：45 tests，0 failures，0 errors，0 skipped |
| 代码覆盖率 | 通过 | 2162/2438 行，88.68%，展示为 89% |
| SSE 与 Session 隔离 | 通过 | `tests/test_api.py`、`tests/test_concurrent_sessions.py` |
| 静态 OpenAPI | 通过 | `docs/openapi.json`，版本 0.3.0，28 个路径 |
| 架构图、状态图、数据流图 | 通过 | `docs/architecture.md` |
| Benchmark 与 bad case | 通过 | 七策略消融报告、1/1 bad case 回归报告 |
| 桌面/移动运行截图 | 通过 | 基础界面与辅导批改共 4 张实测截图 |
| 浏览器演示视频 | 通过 | `docs/assets/tutoring-flow.webm`，VP8，248.8 秒 |
| Docker 配置完整性 | 通过 | Dockerfile/Compose 结构化数据静态测试通过 |
| Docker 构建与运行 | 未执行 | 本机未安装 Docker CLI，需在 Docker 主机补验 |

## 本轮真实服务演示

报告：`reports/demo_20260714T140218Z.json`

| 场景 | 实测结果 |
|---|---|
| 服务就绪 | 462 chunks、2 条报警记录、5 个知识点 |
| 知识问答 | `completed`，5 条引用 |
| 故障诊断 | `completed`，执行 `lookup_error_code → manual_retrieval → check_safety_constraint → record_diagnostic_state` |
| 域外问题 | `abstained` |
| 个性化辅导 | `completed`，得分 100，掌握度 `proficient` |
| 隐私清理 | 演示结束删除 7 条合成学习数据 |

首次演示曾暴露完整语料中的歧义：安全资料中的“火星”和高频“要求”形成两个孤立命中，使域外查询误过旧覆盖门槛。修复后 Evidence Judge 对长查询同时要求最低有效词命中数，并新增回归测试；便携检索固定集复测的 Evidence Judge accuracy 仍为 1.0，报告为 `eval_hybrid_rerank_20260714T140039Z.json`。

## 浏览器界面闭环

真实 Chromium 验收完成了“个性化辅导 → 生成练习 → 填写答案 → 提交批改 → 查看个人进度 → 查看教师匿名概览”。结果：

- 练习返回 7 项评分要点，全部命中，得分 100，状态“已经掌握”。
- 个人进度显示 1/5 个知识点已评测、平均掌握度 100%。
- 教师概览只返回知识点级计数和均分，没有学生 ID 或原始答案。
- 浏览器控制台无错误。
- 390px 移动视口下 `scrollWidth=clientWidth=390`，顶部功能导航仍可操作，练习卡宽度 350px。
- 动态状态使用 `aria-live`，表单有显式标签、错误关联和键盘焦点反馈，并尊重 `prefers-reduced-motion`。

## 自动化测试证据

- JUnit：`reports/pytest_20260714T143843Z.xml`
- Coverage XML：`reports/coverage_20260714T143843Z.xml`
- 结果：45 tests，全部通过；总耗时 31.528 秒。
- 覆盖率：88.68%（2162/2438 行）。

报告生成时发现 pytest 在 Windows 以 `mode=700` 创建系统临时目录会造成跨服务用户 ACL 拒绝。测试 fixture 已改用 `runtime/test-runs` 下显式继承 ACL 的隔离目录；两份受 ACL 影响的无效报告已移至 `runtime/failed-test-reports`，不作为验收证据。

## 数字来源索引

- 七策略检索：`reports/comparison_20260714T114720Z.json`
- 便携档修复后复测：`reports/eval_hybrid_rerank_20260714T140039Z.json`
- 故障诊断：`reports/diagnostic_eval_20260714T121915Z.json`
- 个性化辅导：`reports/tutoring_eval_20260714T130857Z.json`
- bad case 回归：`reports/regression_20260714T124719Z_c1c79d88.json`
- HTTP 演示：`reports/demo_20260714T140218Z.json`
- 自动化测试：`reports/pytest_20260714T143843Z.xml`
- 覆盖率：`reports/coverage_20260714T143843Z.xml`
