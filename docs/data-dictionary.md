# 数据字典

## 文档与片段

| 字段 | 含义 |
|---|---|
| `document_id` | 系统生成的稳定文档 ID，前缀 `doc_` |
| `title` | 文档标题，默认取文件名 |
| `document_type` | 教材、操作编程、安全、题库、教师确认资料等 |
| `course` | 课程名称 |
| `equipment_brand` / `equipment_model` | 适用设备品牌和型号；未知时为空 |
| `chapter` / `page` | 来源章节和页码；解析不到时为空，不推测 |
| `version` / `effective_date` | 知识版本和生效日期 |
| `access_scope` | `public` 或班级/内部范围 |
| `content_hash` | 全文 SHA-256，用于重复检测 |
| `chunk_id` | 片段 ID，前缀 `chk_` |
| `chunk_index` | 片段在文档中的顺序 |

## 结构化报警码

| 字段 | 含义 |
|---|---|
| `alarm_id` | 报警记录稳定 ID，前缀 `alarm_` |
| `equipment_brand` / `equipment_models` | 适用品牌和型号列表；`*` 表示来源只核验到品牌范围 |
| `controller_versions` | 已确认适用的控制器版本；未知时为空，不推测 |
| `code` / `title` / `meaning` | 规范化报警码、标题和来源支持的含义 |
| `likely_causes` | 候选原因，不代表最终故障结论 |
| `safe_checks` | 不改变设备状态即可执行或记录的核对项 |
| `forbidden_actions` | 学生不得自行执行的动作 |
| `risk_level` | 结构化记录的最低风险等级 |
| `source_title` / `source_locator` / `source_excerpt` | 来源与定位证据 |
| `version` / `effective_date` / `review_status` | 数据版本、生效日期和审核状态 |
| `content_hash` / `is_active` | 幂等导入与启停状态 |

## Agent 状态

| 字段 | 含义 |
|---|---|
| `request_id` / `run_id` | 请求和运行标识 |
| `session_id` / `user_id` | 会话和用户隔离键 |
| `task_type` | `knowledge_qa`、`fault_diagnosis`、`tutoring`、`other` |
| `normalized_query` | 去除格式噪声、必要时补入当前会话故障上下文的检索查询 |
| `generated_exercise_id` | 辅导任务创建的练习实例 ID；其他任务为空 |
| `required_slots` / `collected_slots` | 故障所需信息及当前已收集值 |
| `tool_plan` / `tool_history` | 计划工具和实际执行记录 |
| `retrieved_evidence` | 文档、`chunk_id`、章节、页码、检索策略、分数组成和片段 |
| `evidence_score` | 查询覆盖、检索支持、来源质量和多样性的综合门控分；不是答案正确率 |
| `evidence_details` | Judge 各子分、冲突、原因和充分性结论 |
| `configuration` | 本次运行的 requested/effective Agent 档、检索策略、Top-k、模型/向量/精排版本和证据阈值快照 |
| `risk_level` | `low`、`medium`、`high`、`critical` |
| `retry_count` / `step_count` | 业务重试与 Trace 事件计数 |
| `transition_count` | 受 `MAX_AGENT_STEPS` 约束的确定性状态转移次数 |
| `decision_history` | 模型节点、Schema、结构化输出、Token、耗时、尝试次数、校验与 fallback；不含密钥和完整 Prompt |
| `field_provenance` | 关键字段来自当前用户、会话历史或确定性提取器 |
| `model_usage` | 本次运行累计输入/输出 Token、调用次数和可选估算成本 |
| `stop_reason` | 等待、完成、拒答或转交的机器可读停止原因 |
| `final_status` | 等待补充、完成、拒答、转交或失败等最终状态 |
| `current_hypotheses` | 结构化报警记录给出的候选原因；始终标记为尚未确认 |

`diagnostic_states` 以 `run_id` 保存设备、报警码、匹配状态、候选原因和下一步动作，供 Trace 与后续轮次回放。正式学生身份仍只保存在隔离的运行状态中。

## 知识点、练习与掌握度

| 实体 / 字段 | 含义 |
|---|---|
| `knowledge_points` | 版本化知识点目录，含名称、别名、来源查询、题目模板和教师评分要点 |
| `criteria` | 评分要点及可接受关键词；只对教师和服务端批改开放 |
| `exercises` | 面向单个学生的练习实例，保存知识点、题目、难度、来源引用和状态 |
| `exercise_attempts` | 学生答案、得分、命中/缺失要点和解释；练习只允许提交一次 |
| `mastery_score` | 同一学生与知识点历次得分的累计平均值 |
| `mastery_status` | `not_started`、`needs_review`、`developing` 或 `proficient` |

删除个人学习记录时，系统同时删除该学生的练习、作答和掌握度。班级聚合不返回用户 ID。

## Trace 与 bad case

Trace 的规范版本为 `2.0.0`，JSON Schema 见 `docs/trace-schema.json`。所有字符串字段（包括模型结构化输出、事件和工具参数）在导出时递归执行手机号、邮箱脱敏。

负向反馈（`helpful=false` 或评分不高于 2）会创建 `bad_case_id`。`bad_case_assertions` 保存教师审核的预期状态、任务类型、风险等级、答案包含/排除短语、引用要求和审核说明。`regression_cases` 保存晋升时的不可变导出包及 `package_hash`，避免后续原始记录变化悄悄改变回归输入。

CourseOps 导出包 Schema 见 `docs/bad-case-schema.json`。包中包含脱敏输入、多轮前置消息、原始结果、反馈、审核断言、配置快照和完整 Trace。

## 运营统计

`GET /api/v1/operations/metrics` 仅返回聚合结果：窗口内运行总数、状态/任务分布、P50/P95 总耗时、工具调用/错误/超时、安全事件风险分布、反馈帮助率/平均评分和 bad case 状态分布，不返回用户标识。
