# ADR-0009：在确定性控制平面内引入 LangGraph 决策层

状态：已采纳（v0.4.0）

## 背景

v0.3.0 使用显式有限状态机、规则路由和本地检索，工程链路稳定，但不能支持“基于 LangGraph 的 Agentic RAG”这一口径。直接改成自由 Agent 又会把安全、报警适用范围和人工转交交给概率模型。

## 决策

保留 portable 状态机基线，增加 `agentic-online` 与 `agentic-quality`。LangGraph 只编排以下结构化模型节点：意图识别、查询重写、槽位提取、关键澄清、只读工具计划和 Evidence Support。

以下能力始终确定性执行：高风险预检、关键槽位原文 span 校验、工具允许列表与参数重建、报警品牌/型号/控制器适用范围、最大图步数、重试预算、最终 Evidence gate、权限、人工转交和学习记录删除。

模型 Evidence Judge 只能把 `sufficient` 从真降为假，不能把确定性失败改为通过。调用失败允许按配置降级到 portable，但必须写入 Trace，不得静默伪装为 Agentic 成功。

## 后果

- 简历可准确表述为“受显式状态机约束的 LangGraph Agentic RAG”，但真实模型效果只能在真实模型烟测后声明。
- portable 回归不依赖网络或密钥。
- Agentic Trace 会增加 Token、延迟和成本字段，且需要单独评测模型供应商与模型版本。
- 自由 LLM Agent 仅作为隔离 Benchmark 对照，不进入面向学生的生产路径。
