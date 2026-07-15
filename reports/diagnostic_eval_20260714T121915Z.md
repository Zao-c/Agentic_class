# 故障诊断任务评测

- 运行 ID：`diagnostic_eval_20260714T121915Z`
- 数据集：`diagnosis_eval_v1.json`

| 指标 | 实测值 |
|---|---:|
| case_count | 7 |
| final_status_accuracy | 1.0 |
| slot_collection_completeness | 0.8571 |
| clarification_effectiveness | 1.0 |
| tool_selection_accuracy | 1.0 |
| alarm_match_accuracy | 1.0 |
| normal_completion_rate | 1.0 |
| average_interaction_turns | 1.14 |
| risk_escalation_accuracy | 1.0 |
| unsafe_advice_rate | 0.0 |

> 用例在临时 SQLite 数据库中端到端运行，不写入正式学生记录。