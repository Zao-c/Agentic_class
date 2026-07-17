# 典型 bad case：负向反馈不能直接成为正确答案

## 背景

一次示教编程知识问答收到负向反馈。系统创建 bad case：

- `bad_case_id`：`bad_0abf5712dc824a279b393ef4f51bf322`
- 晋升后的回归 ID：`reg_878607df4dec27d385ddb32e`

问题不在于“把原答案保存下来重放”，而在于负向反馈只说明用户不满意，并没有说明什么结果才正确。如果直接把原状态或原回答当金标，会把错误行为固化。

## 处理

教师先填写可执行断言：

- 最终状态必须为 `completed`。
- 任务必须路由为 `knowledge_qa`，风险为 `low`。
- 回答必须包含“课程资料”。
- 回答不得包含无关的“量子引力”。
- 必须返回课程引用。

导出包包含脱敏输入、原始结果、反馈、配置和完整 Trace，并生成稳定 `package_hash`。维护者随后在隔离 SQLite 中复制当前知识快照并重放，避免污染正式运行和学习记录。

## 实测结果

最新回归报告 `reports/regression_20260714T124719Z_c1c79d88.json`：

- 1 条用例，1 条通过。
- 7 项断言全部通过。
- 重放结果为 `completed / knowledge_qa / low`。
- 返回 5 条引用，执行 1 次检索工具。

## 学到什么

这是一个数据治理问题，不只是测试问题：反馈、期望行为和回归金标必须分离。教师审核增加了人工成本，但避免了系统用错误输出训练自己。当前断言仍以状态、短语和引用存在性为主，复杂答案质量仍需要教师评分量表或受控语义评估。

---

# 典型 bad case：受控 Agent 仍会发生意图漂移与证据拒答型 DoS

## 背景

首次 50 条合成诊断三方案真实运行中，受控 LangGraph 完成率为 0.68、不安全建议率为 0，但 16 条用例没有满足全部工程断言。逐例检查发现这些结果不是同一种问题：

- 5 条多轮补槽/撤回任务被模型从 `fault_diagnosis` 降级为 `knowledge_qa` 或 `other`，错误进入课程检索。
- 7 条业务状态和安全结果正确，但模型改写遗漏已经确认的设备、报警码或运行模式。
- 3 条只是 Benchmark 对“未确认/未知”字面要求过严，不是槽位污染。
- 1 条结构化报警已经 `exact_match`，但污染文档进入 Evidence LLM 后使模型降低置信度，最终保守转交。

原始模型观测和评分口径修正分别保存在 `reports/diagnosis_three_way_engineering_raw_v1.json` 与 `reports/diagnosis_three_way_engineering_rescored_v2_1.json`，没有删除失败样本。

## 控制面修复

1. LangGraph 在意图分支选择前同时比较模型意图与确定性诊断信号；明确诊断任务不能被模型降级。Trace 记录 proposed、deterministic、effective 和覆盖原因。
2. 模型 Query Rewrite 必须保留经过验证的设备、报警码、运行模式和控制器槽位，并明确未确认槽位；模型补造的 `robot_control`、打开控制柜、旁路或强制运动等受限动作会触发确定性回退。
3. 诊断 Evidence LLM 只接收结构化报警证据。普通文档命中间接 Prompt 注入规则或红队类型后，只在隔离 Trace 中保存文档 ID、规则与 excerpt SHA，不进入模型上下文、回答或引用。
4. 同码多条审核记录按最高风险和全部禁做事项合并；未知报警没有结构化匹配时，不再把相似但无关的手册片段公开为引用。
5. Benchmark runner 保存逐轮任务、改写、槽位、工具、状态、fallback 和停止原因，避免只看最终状态猜测根因。
6. Evidence Judge 仍执行并保留原始输出；当报警库同时满足 `exact_match` 和 `source_verified` 时，模型的假阴性只能作为建议，确定性控制面保留有效证据结论。Trace 同时记录 proposed/effective 和覆盖原因；型号未核验或来源未核验时模型仍可降低置信度。

## 验证边界

- 故意返回错误意图、补造危险改写、接收污染文档和错误否决权威证据的 fake provider 回归均已通过。
- 50 条 portable 任务重新执行后仍为 completion 1.0、unsafe advice 0.0，并已包含逐轮观测。
- 相同 50 条任务已完成一次独立 DeepSeek 受控-only 复测：completion 0.94、unsafe advice 0、fallback 0，意图/改写/槽位/工具执行均为 1.00；原始观测保存在 `reports/diagnosis_controlled_post_hardening_v1.json`。
- 该报告中的 3 条未完成样本都由 Evidence Judge 假阴性导致；上述第 6 项修复发生在报告之后，只完成确定性 fake 回归，因此不能把它外推为 1.00 在线完成率。
- 全量测试项数和覆盖率以当前 CI 徽章及测试报告为准。
