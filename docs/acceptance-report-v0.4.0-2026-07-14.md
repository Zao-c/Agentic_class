# v0.4.0 受控 Agentic 与候选数据验收报告

日期：2026-07-14

## 结论

- `portable` 保持默认路径，原有任务与 API 回归通过。
- `agentic-online` / `agentic-quality` 已接入真实 LangGraph `StateGraph` 与 OpenAI 兼容结构化模型适配器。
- 结构化模型节点覆盖意图、查询重写、槽位、澄清、工具计划和 Evidence Support；高风险请求在模型调用前由确定性规则拦截。
- 模型虚构的型号无法通过用户原文 span 校验；模型工具计划由允许列表过滤，实际参数由控制平面重建。
- 使用进程内临时密钥完成 DeepSeek V4 Flash 真实 HTTP 端到端烟测；密钥未写入配置、Trace 或报告。
- 从真实课程题库抽取 132 条 `candidate` QA：判断 51、单选 63、带参考答案实训任务 18。全部标记 `needs_teacher_review`，不计入正式指标。

## 自动化证据

| 项目 | 结果 | 报告 |
|---|---:|---|
| 测试 | 54 passed | `reports/pytest_v0.4.0.xml` |
| app 覆盖率 | 89.65%，四舍五入 90%（2486/2773 statements） | `reports/coverage_v0.4.0.xml` |
| Agentic 安全预检 | critical 请求模型调用数为 0 | `tests/test_agentic_graph.py` |
| 槽位原文校验 | 虚构 `IRB9999` 被拒绝 | `tests/test_agentic_graph.py` |
| 模型适配器 | Schema、Token、成本、重试、缺 key 均覆盖 | `tests/test_decision_provider.py` |
| 候选数据 | 132 条、唯一 ID、来源哈希、不得计指标 | `tests/test_candidate_data.py` |
| 真实 LangGraph 预检 | 4 节点、1603 Token、约 5.62 秒、无 fallback | `reports/agentic_smoke_20260714T155846Z.json` |
| 真实 HTTP 诊断 | 5 模型节点 + 4 确定性工具、2803 Token、约 7.85 秒、状态 completed | `reports/agentic_http_smoke_20260714T160059Z.json` |
| 真实 quality 组合 | DeepSeek + BGE + Cross-Encoder、2779 Token、神经检索约 3.36 秒、状态 completed | `reports/agentic_http_smoke_20260714T160740Z.json` |

首次 DeepSeek JSON Mode 调用返回了合法 JSON，但把 `task_type` 写成 `task`，暴露了 Fake provider 无法发现的兼容问题。修复方式是把完整 Pydantic JSON Schema 显式加入系统提示；修复后5个模型节点均在第一次尝试通过校验。

## 尚未通过的门

1. 同一冻结任务集上的批量在线 Agent 评测。
2. 规则状态机、自由 LLM Agent、约束 LLM Agent 三方案的完成率、工具选择、安全转交、P95、Token 和成本对比。
3. ABB IRB 120 + IRC5C 官方事件/产品手册与 RobotWare 版本确认。
4. 132 条候选 QA 的教师逐条审核与 gold 晋升。
5. 30–50 条真实故障任务和 30 条真实/脱敏 bad case；当前真实学员/生产 bad case 为 0。
