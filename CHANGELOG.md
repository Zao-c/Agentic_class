# Changelog

## Unreleased

- 使用 50 条合成诊断任务完成受控 LangGraph 三次、共 150 条 DeepSeek 真实模型观测：completion 均值 0.9933、总体标准差 0.0094，fallback/runner error/unsafe advice 均为 0；唯一混合 case 是 Query Rewrite 偶发遗漏 RobotWare 版本未知事实。
- Query Rewrite 控制面新增最近一次 RobotWare 版本不确定性保真；模型漏写用户明确的未知/未确认状态时补回规范标记并记录调整原因，后续明确版本可覆盖旧不确定性。
- Benchmark 报告 Schema 升级至 1.2：新增逐 repetition 完整矩阵、均值/总体标准差/min/max、稳定性声明门槛、跨轮混合结果和语义失败族；重评分器会拒绝缺失或重复的 case/repetition 矩阵。
- 使用 50 条公开合成诊断任务完成 portable 三次、共 150 条观测的无模型复现；completion 均值 1.00、总体标准差 0、跨轮混合 case 0，P50/P95 的跨轮均值为 1.10/1.85 秒。该结果只验证统计与规则契约，不是诊断准确率。
- 使用相同 50 条合成诊断任务完成受控 LangGraph 的 DeepSeek post-hardening 复测：completion 由 0.68 提升至 0.94，fallback 由 0.02 降至 0，意图、改写、槽位、工具执行与引用均为 1.00；独立报告保留全部逐题观测和工程声明边界。
- 复测发现 3 条权威精确匹配被 Evidence Judge 假阴性转交；控制平面现保留模型否决，但对 `source_verified + exact_match` 采用确定性有效结论，并在 Trace 记录 proposed/effective 与覆盖原因。未核验型号、缺失证据和高风险记录仍 fail-closed。
- 根据首次三方案真实运行的 16 个受控 Agent 失败逐例修复控制面：LangGraph 分支前增加诊断意图下限、Query Rewrite 槽位保真与受限操作反补造，并把 proposed/effective/override 原因写入 Trace。
- 诊断 Evidence Judge 只接收结构化报警证据；普通文档命中 Prompt 注入规则或红队类型时进入隔离 Trace，不参与模型输入、回答或引用。未知报警不再公开无关引用，同码记录按最高风险与禁做事项合并。
- Portable/受控 Benchmark runner 新增逐轮 task、rewrite、slots、proposed/executed tools、status、fallback 与 stop reason；50 条 portable 报告已重新执行并保持 completion 1.0、unsafe advice 0.0。
- 完成 50 条合成多轮诊断集的首次 DeepSeek 三方案真实工程烟测，公开 portable、自由 Agent 与受控 LangGraph 的完成率、安全、P95、Token、成本和 fallback 原始观测。
- Benchmark Protocol 升级至 v2.1：分离模型提议工具与控制平面执行工具契约、规范化自由 Agent 槽位别名，并修复多轮未授权工具拦截率分母。
- 新增无模型调用的报告重评分器，同时保留 v2.0 原始报告与 v2.1 重评分报告及原报告 SHA，避免把评分口径修正伪装成模型质量提升。
- ABB IRC5 结构化报警库由 2 条扩充至 9 条，新增急停、碰撞、碰撞退回失败、未校准、未同步、SMB 数据异常和未验证路径事件；所有新增事件均按高风险只读解释与人工转交处理。
- 对 ABB `3HAC020738-001` Revision K 的印刷页 79、80、81、82、91 完成 PDF 可视核验，并发布原创事件摘要；官方 PDF 仍不进入仓库。
- 新增 ABB IRB 120 / IRC5 官方来源元数据注册表，固定三份 ABB 文档的文档号、Revision、官方 URL、PDF SHA256 和印刷页；仅发布原创摘要，不转载官方 PDF，也不把课程参考配置冒充学校实机版本。
- 新增 50 条、10 个语义族的合成多轮故障诊断工程集及可重建 manifest，覆盖追问、缺失信息、型号冲突、资料缺失、Prompt 注入、槽位污染与安全绕过；明确禁止冒充教师 Gold。
- GitHub Actions 增加 50 条 portable 诊断全量执行门禁，固定验证任务完成率 1.0 与不安全建议率 0.0；该门禁只表示合成协议回归，不是诊断准确率宣传。
- Benchmark 期望新增 `tools_by_runner`，在相同任务语义下按各 runner 的真实允许工具集合严格评分；命令行新增 `--knowledge-root`，可强制公开评测只读取指定公开语料。
- 新增 10 份公开原创合成微型教材与 180 条确定性学生问法检索集，按 60 个 semantic family 切分为 108/36/36，并记录生成器、seed、数据与来源 SHA256。
- 新增隔离合成检索 Benchmark 运行器和四策略公开报告；报告只含聚合工程指标，不含绝对路径、真实学生数据或教师 Gold 声明。
- Benchmark 数据治理门禁新增来源、角色、标签权威、指标资格与正式比较资格交叉校验，机器拒绝 synthetic/模拟角色冒充教师 Gold。

- 新增 6 类可执行系统红队 Harness：离线注入畸形 JSON、Timeout、429、超长冲突工具结果、跨用户访问和自由 Agent 写工具，并发布脱敏工程报告。
- 自由 Agent 工具结果增加 8000 字符送模上限与截断元数据，防止超长不可信工具内容无界进入模型上下文。
- 新增候选 QA 质量 lint：校验唯一 ID、必填字段、来源哈希、审核状态及 dedup/leakage group 跨 split 泄漏；重复题仅进入教师待审 warning。
- 发布不含题面内容的 132 条候选快照聚合证据与 Schema，公开仓库可核验类型分布、来源数、重复率和私有快照 SHA256，同时保持 `teacher_reviewed=false` 与 `metric_eligibility=false`。
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
