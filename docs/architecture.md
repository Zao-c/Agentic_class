# 系统架构与状态流

## 组件关系

```mermaid
flowchart LR
    U["学生 / 教师"] --> API["FastAPI + Web UI"]
    API --> PF{"Agent Profile"}
    PF -->|portable| WF["显式状态机决策"]
    PF -->|agentic| LG["LangGraph + LLM 结构化决策"]
    LG --> WF
    WF --> RT["课程与手册检索"]
    WF --> AC["结构化报警码门控"]
    WF --> SJ["Evidence Judge"]
    WF --> SS["安全规则"]
    WF --> ES["教师转交"]
    RT --> KB["data/active + SQLite chunks"]
    AC --> AK["data/structured + SQLite alarm_codes"]
    WF --> TS["规则型辅导服务"]
    TS --> KP["知识点 / 练习 / 掌握度"]
    WF --> DB["SQLite Run / Event / Feedback"]
    DB --> TR["Trace JSON / bad case"]
    TR --> CO["CourseOps Agent Harness"]
    TR --> BR["审核断言 → 隔离重放 → 回归集"]
    API --> EV["固定评测入口"]
    EV --> RP["JSON + Markdown 报告"]
```

LangGraph 是模型决策平面，显式状态机是确定性控制平面。模型节点只负责意图、查询重写、槽位提议、澄清问题、只读工具计划和证据支持判断；安全预检、关键实体原文校验、工具允许列表、报警适用范围、最终证据门、转交和权限不交给模型。工具计划按 `proposed_plan → validated_plan → executed_plan` 进入 Trace，只有通过 Schema、任务允许列表和字段来源检查后的参数才可能参与真实执行。

## 故障诊断链路

```mermaid
flowchart LR
    F["故障描述"] --> P["确定性高风险预检"]
    P -->|高风险| X["教师转交"]
    P -->|可继续| LLM["LLM 结构化提取 + 原文 span 校验"]
    LLM --> S["收集品牌型号 / 报警码 / 运行模式"]
    S -->|缺少槽位| Q["一次追问一个关键字段"]
    S -->|完整| L["lookup_error_code"]
    L --> M["编号 + 品牌 + 型号范围门控"]
    M -->|唯一且已核验| R["补充手册检索"]
    M -->|未收录 / 冲突 / 范围不符| X
    R --> C["check_safety_constraint"]
    C -->|高风险| X
    C -->|风险可控| D["候选原因 + 安全核对项"]
    D --> P["record_diagnostic_state"]
    P --> O["等待操作结果反馈"]
```

结构化报警码是决定能否继续诊断的主证据；通用 RAG 只补充来源，不得覆盖结构化范围冲突。

## 个性化辅导链路

```mermaid
flowchart LR
    U["学生出题请求"] --> P["读取本人知识点掌握度"]
    P --> W["显式知识点 / 别名 / 最低掌握度规则"]
    W --> R["检索课程来源"]
    R --> E["创建练习实例并隐藏评分要点"]
    E --> A["学生提交答案"]
    A --> G["教师要点与同义词逐项评分"]
    G --> X["已覆盖 / 待补充 / 来源解释"]
    G --> M["累计平均掌握度"]
    M --> S["学生进度 / 班级匿名聚合"]
```

## 检索链路

```mermaid
flowchart LR
    Q["独立检索查询"] --> B["BM25"]
    Q --> E["TF-IDF → LSA 密集向量"]
    B --> R["RRF 融合"]
    E --> R
    R --> C["Top-30 候选"]
    C --> F["可解释特征精排"]
    F --> T["Top-5 引用"]
    T --> J["Evidence Judge v2"]
    J --> A["回答 / 拒答 / 转交"]
```

## 请求与数据流

```mermaid
flowchart TD
    C["Web / API 客户端"] -->|ChatRequest| F["FastAPI"]
    F -->|创建 Run| S[("SQLite 状态库")]
    F --> W["AgentWorkflow"]
    W -->|检索查询| K[("课程 chunks")]
    W -->|报警编号与设备范围| A[("结构化 alarm_codes")]
    W -->|辅导知识点| P[("knowledge_points")]
    W -->|事件 / 工具 / 状态| S
    W -->|练习与作答| M[("exercises / mastery")]
    S -->|SSE 事件| C
    W -->|答案与引用| C
    S -->|脱敏 Trace| H["CourseOps / bad case"]
    H -->|教师断言| R["隔离回归"]
    K -->|只读快照| R
    A -->|只读快照| R
    P -->|只读快照| R
```

## 有限状态机

```mermaid
stateDiagram-v2
    [*] --> queued
    queued --> classify_intent
    classify_intent --> normalize_query
    normalize_query --> check_required_context
    check_required_context --> waiting_for_user: 缺少关键槽位
    check_required_context --> check_safety: 命中高风险表达
    check_required_context --> plan_tool_calls: 信息完整
    plan_tool_calls --> execute_tool
    execute_tool --> judge_evidence
    judge_evidence --> abstained: 课程问答证据不足
    judge_evidence --> check_safety: 证据充分或故障任务
    check_safety --> escalated: 高风险 / 故障证据不足
    check_safety --> compose_answer: 风险可控且证据充分
    compose_answer --> completed
    waiting_for_user --> [*]
    abstained --> [*]
    escalated --> [*]
    completed --> [*]
```

状态最多执行 `MAX_AGENT_STEPS` 次控制平面转移；Trace 事件数量不再冒充状态步数。Agentic 图还使用 LangGraph `recursion_limit`。每次模型决策记录 Schema、结构化输出、简短依据、字段来源、Token、估算成本、耗时、尝试次数和 fallback；每个工具调用记录名称、实际参数、逐参数来源与校验状态、耗时、重试和结构化错误。模型提议被覆盖或拒绝时，Trace 保留原提议与机器可读调整原因。

## 访问与隐私

- Run 和 SSE 必须使用创建请求时的 `user_id`，其他用户返回 403。
- Trace 对学生仅开放自己的请求；教师和维护者可读取。
- Trace 导出对用户 ID 做 SHA-256 截断匿名化，并脱敏手机号和邮箱。
- Trace、反馈、bad case 标签和工具事件中的字符串均递归脱敏。
- 班级学习与掌握度视图只返回知识点、结果、人数和统计值，不包含学生标识。
- 学生可读取和清除自己的学习记录、练习、作答和掌握度。
- 该删除接口不是全量账户数据删除；Run、事件、反馈和 bad case 仍受单独保留策略约束。
- 学生答题前的知识点和练习接口不返回内部评分要点。

## Bad case 回归流

```mermaid
flowchart LR
    F["负向反馈 / 运行失败"] --> B["bad case"]
    B --> V["教师填写行为断言"]
    V --> E["稳定哈希导出包"]
    E --> I["复制当前知识到隔离 SQLite"]
    I --> R["重放多轮上下文与目标请求"]
    R --> C["逐条校验状态 / 风险 / 文本 / 引用"]
    C -->|通过审核| P["晋升 regression case"]
    P --> A["一键自动回归报告"]
```

重放目录由应用在 `runtime/replays` 下显式创建并继承父目录权限，结束后只删除本次 UUID 子目录。正式数据库不写入重放 run、事件或学习记录。
