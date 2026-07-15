# 工具契约

所有工具通过 `ToolRuntime` 执行，统一获得超时、有限重试、连续失败熔断和结构化错误。Trace 的 `tool.finished` 事件记录工具名、脱敏参数摘要、执行状态、尝试次数、耗时及错误代码。

Agentic 档的计划分为三层：

- `proposed_plan`：模型原始提出的工具、参数和理由。
- `validated_plan`：经过严格 Pydantic Schema、任务允许列表和参数可信来源校验的计划；未知字段删除，补造或冲突字段由用户原文/确定性状态覆盖，缺失的必需只读工具由控制平面补齐。
- `executed_plan`：实际传入工具的参数、逐字段来源、校验状态、耗时、重试和结果。

三层同时保存在 `configuration.agentic_tool_plan`，`adjustments` 记录每次删除、覆盖、拒绝或补齐的机器可读原因。安全检查、诊断状态持久化和练习生成仍由确定性控制平面拥有最终决定权。

| 工具 | 输入 | 输出 | 默认权限 | 重试条件 | 敏感字段 |
|---|---|---|---|---|---|
| `course_retrieval` | `query`, `top_k`, `strategy`, `access_scope` | 带 `chunk_id`、策略和分数组成的 `Citation[]` | student | 超时、连接错误 | 原始查询仅进入受控 Trace |
| `manual_retrieval` | `query`, `equipment`, `strategy` | `Citation[]` | student | 超时、连接错误 | 设备序列号不得记录 |
| `lookup_error_code` | `code`, `equipment_brand`, `equipment_model`, `controller_version` | 匹配状态、结构化报警记录、可用设备范围 | student | 连接错误 | 不记录设备序列号 |
| `check_safety_constraint` | `query`, `evidence_sufficient`, `alarm_risk` | 风险等级、是否转交、原因和限制 | student | 不重试 | 原始查询仅进入受控 Trace |
| `record_diagnostic_state` | `run_id`, `lookup_status`, `next_action` | 状态更新时间与下一步 | student | 连接错误 | 设备标识按课程范围存储，不存序列号 |
| `citation_resolver` | `document_id`, `chunk_id` | 文档、章节、页码 | student | 超时 | 无 |
| `teacher_escalation` | `run_id`, `risk_level`, `reason` | `escalation_id` | student | 连接错误 | 用户标识匿名化 |
| `get_student_profile` | 当前所有者、知识点目录 | 不含无关会话内容的知识点掌握度 | owner/teacher | 连接错误 | `user_id` 不进入工具参数日志 |
| `get_learning_history` | `user_id`, `limit` | 学习记录 | owner/teacher | 连接错误 | `user_id` |
| `identify_weak_topics` | 掌握度、知识点目录 | 按规则排序的薄弱知识点 | owner/teacher | 不重试 | 无 |
| `generate_exercise` | `knowledge_point_id`, `difficulty`, 课程检索证据 | 持久化练习、来源引用 | owner/teacher | 超时、连接错误 | 答题前不返回评分要点 |
| `grade_answer` | `exercise_id`, 所有者答案 | 得分、逐项命中、待补充项、引用 | owner | 不重试 | 原始答案仅存于所有者记录 |
| `update_learning_progress` | 知识点、当前得分、历史次数 | 累计掌握度与状态 | owner | 连接错误 | 班级视图只聚合 |

当前 `web_search` 保持禁用。启用前必须补充允许域名、内容时效、引用解析、提示注入防护和出错降级策略。

`lookup_error_code` 的匹配状态：

- `exact_match`：编号、品牌和型号范围精确匹配。
- `brand_match_model_unverified`：编号和品牌匹配，但来源只提供品牌范围。
- `ambiguous`：同一适用范围存在不同含义，必须转交。
- `model_required` / `model_mismatch` / `brand_mismatch`：设备范围不足或冲突，必须转交。
- `not_found`：结构化库未收录，不允许用相似编号猜测。

结构化错误：

- `TOOL_TIMEOUT`：工具超过配置时间。
- `TOOL_EXECUTION_FAILED`：工具返回不可重试异常，或重试耗尽。
- `TOOL_CIRCUIT_OPEN`：同一工具连续失败达到阈值，恢复窗口前快速失败。
