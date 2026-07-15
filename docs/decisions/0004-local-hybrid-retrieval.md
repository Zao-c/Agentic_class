# ADR-0004：默认使用本地混合召回与可解释特征精排

- 状态：已采纳
- 日期：2026-07-14
- 取代：ADR-0002 中的单一词法检索运行配置；ADR-0002 仍作为历史基线保留

## 决策

默认检索链路使用：

```text
BM25 + TF-IDF→LSA 密集向量
→ Reciprocal Rank Fusion
→ Top-30 候选
→ 来源、覆盖、标题、步骤结构和设备型号特征精排
→ Top-5
→ Evidence Judge v2
```

特征精排明确命名为 `feature_reranker_v1`，不得称为 Cross-Encoder。神经 Embedding 和 Cross-Encoder 只有在模型版本、下载方式、硬件要求和真实评测收益均可复现后才能替换当前实现。

## 实测依据

在 `rag_eval_v1.csv` 的 12 条样本上，`hybrid_rerank` 实测 source_hit@5 为 1.0、nDCG@5 为 0.927、Recall@5 为 0.8、Evidence Judge accuracy 为 1.0。它的 MRR 为 0.72，低于纯 Embedding 的 0.77，因此选择它是为了优先保证 Top-5 来源覆盖，而非声称所有指标都最优。

完整报告：`reports/comparison_20260714T112350Z.json`。

## 代价

- 首次查询需要构建本地 LSA 索引。
- 片段快照缓存后 P50 检索延迟为 52.35 ms，高于纯 Embedding 的 24.24 ms。
- 小评测集不能代表生产准确率，需要持续加入真实 bad case。
