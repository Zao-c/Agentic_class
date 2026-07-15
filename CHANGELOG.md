# Changelog

## Unreleased

- Benchmark Protocol 升级为 v2.0.0：三种 runner 统一逐轮执行，自由 Agent 不再提前看到未来轮次。
- 自由 Agent 引用改为从实际工具结果派生；模型自报引用、拒答与转交只保留为审计 metadata。
- 受控 Agent fallback 拆分为独立指标并取消横向比较资格；正式模式强制三 runner、至少三次重复并关闭 portable fallback。
- 报告新增语料、报警库、知识点、非敏感配置和模型配置指纹，以及 proposed/executed/blocked 工具分层统计。
- 公开仓库与 GitHub Actions 已完成发布，测试和 Docker 健康检查均进入持续集成。

## 0.5.0 - 2026-07-15

- 新增候选 QA → 教师审核 → 审计 → 不可覆盖 Gold 的数据治理流水线；人工声明、三项检查、split 与来源哈希是强制门禁，当前没有伪造 Gold。
- 新增 portable、隔离自由 LLM Agent、LangGraph 受控 Agent 的统一冻结 Benchmark Harness，支持多次运行、Token/成本、P50/P95、fallback 与逐题失败分析。
- 新增 12 条工程比较集、8 条聊天红队集和 6 类系统故障注入规格；全部明确未经教师审核，不进入正式质量宣传。
- LLM 工具提议升级为 `proposed_plan → validated_plan → executed_plan`；Pydantic、任务允许列表和可信字段来源决定实际执行参数。
- portable 单次工程验证公开保留失败：主集与聊天红队任务完成率均为 0.50，并定位型号伪造、多轮槽位污染、危险意图和冲突证据问题。
- 针对上述红队失败增加统一工具前安全预检、假设实体不可信、显式历史撤回和操作性安全状态路由；受控 Agent 同步隔离已撤回历史。同一 8 条集复测为 1.00，并同时保留修复前后报告，避免隐藏 bad case。
- 初始化本地 Git 仓库，增加 GitHub Actions、公开发布扫描、作品展示许可证、公开合成样例与课程资料 Git 隔离规则。
- Docker 镜像不再复制本地 `data/active` 或评测集；新增真实 Docker build/up、三任务、状态卷与重启恢复验收脚本。本机因无 Docker CLI 仍未完成真实容器验收。
- README 精简为招聘方首屏、核心证据、三方案结论、快速启动、安全边界和文档索引。

## 0.4.0 - 2026-07-14

- 引入真实 LangGraph 决策平面、OpenAI 兼容 LLM 结构化输出、允许列表工具计划和显式 portable fallback。
- 增加 DeepSeek V4 Flash 在线档，以及 DeepSeek + BGE + Cross-Encoder 质量档真实 HTTP 单条烟测。
- Agent Trace 记录 Schema、字段来源、模型 Token、估算成本、耗时、重试、fallback 与停止原因。
- 从课程题库抽取 132 条候选 QA，全部标记 `needs_teacher_review` 且不进入正式指标。

## 0.3.0 - 2026-07-14

- 新增 5 个版本化知识点、持久化练习实例、确定性逐项批改和知识点掌握度。
- Agent 辅导链路显式记录学生画像、薄弱项选择、课程检索和练习生成工具。
- 新增学生练习/进度 API、教师匿名班级聚合，以及个人学习数据整体删除。
- 新增 4 条辅导闭环评测集、隔离评测服务和 JSON/Markdown 报告。
- 新增全场景 HTTP 演示、静态 OpenAPI 导出、快速启动、项目案例和典型 bad case 文档。
- 修复 Docker 镜像及 Compose 未包含结构化报警码和知识点目录的问题。
- Evidence Judge 增加长查询最小有效词命中数，修复完整语料中“火星/要求”等孤立歧义词导致的域外误答。
- 测试临时目录改为工作区内显式继承 ACL，避免 Windows pytest `mode=700` 目录在服务用户间不可访问。
- 浏览器界面接入练习填写、逐项批改反馈、个人知识点进度和教师匿名班级概览。
- 增加跳转链接、显式表单标签、动态状态播报、可见焦点和减少动画支持；390px 实测无横向溢出。
- 新增辅导桌面/移动截图和真实浏览器闭环 WebM 录屏。

## 0.2.0 - 2026-07-14

- 新增标准 BM25、TF-IDF→LSA 本地密集向量、RRF 混合召回和可解释特征精排。
- 引用增加 `chunk_id`、检索策略和分数组成，Trace 保存完整检索配置快照。
- Evidence Judge v2 增加显著词覆盖、来源质量、多样性、安全冲突及型号/报警码精确命中约束。
- 新增 `rag_eval_v1.csv`，覆盖 10 条域内问题和 2 条资料缺失问题。
- 新增 Recall@5、MRR、nDCG@5、Evidence Judge accuracy 和四策略一键消融报告。
- 修复最低正分候选在 min-max 归一化后被错误过滤的问题。
- 增加进程内片段与向量索引快照缓存，文档导入时自动失效。
- 修复同一会话并发请求读取排队中兄弟任务、以及“示教编程”被误识别为“示教模式”的问题。
- 新增版本锁定、本地只读的 BGE Embedding 与 BGE Cross-Encoder 检索后端，以及磁盘向量索引缓存。
- 新增 `portable`、`neural-online`、`neural-quality` 三个启动档位和神经索引预热脚本。
- 七策略同口径消融表明：便携默认保留 `hybrid_rerank`，当前机器在线增强采用 `neural_hybrid`，Cross-Encoder 仅用于高质量档。
- 新增版本化结构化报警码表、精确设备范围匹配、冲突识别、诊断状态持久化和教师管理 API。
- 故障 Agent 现在显式执行 `lookup_error_code`、手册检索、安全检查和诊断状态记录；未收录、范围冲突和高风险结果均转交。
- 新增 7 类故障诊断端到端评测及任务级指标，评测使用临时 SQLite，不写入正式学习记录。
- 修复 SQLite 事务结束后连接未显式关闭，以及工具成功/异常结束后工作线程未确定性退出的问题。
- 新增 bad case 详情、教师行为断言、稳定哈希 CourseOps 导出包、隔离重放和回归用例晋升。
- 新增 bad case 一键回归 JSON/Markdown 报告及命令行入口。
- 新增运行延迟、状态、任务、工具错误/超时、安全事件、反馈和 bad case 聚合统计 API。
- Trace、反馈、bad case 标签及事件工具参数改为递归脱敏；修复中文文本紧邻手机号时边界无法识别的问题。
- 隔离运行目录改为显式继承父目录 ACL，修复 Windows 服务进程使用 `tempfile` 后无法打开临时 SQLite 的问题。

## 0.1.0 - 2026-07-14

- 建立 FastAPI、SQLite、SSE 和浏览器界面。
- 实现三类任务的显式有限状态机。
- 实现故障槽位补全、安全拦截和教师转交。
- 实现课程资料解析、哈希去重、本地检索、引用和证据不足拒答。
- 实现 Trace、反馈、bad case、学习记录和评测报告。
- 实现统一工具超时、重试、熔断和结构化错误。
- 完成 Docker、架构/决策/数据字典/工具契约和已知限制文档。
- 在浏览器验收中修复流程答案错误抽取“适用问题”的问题，改为保留编号步骤。
