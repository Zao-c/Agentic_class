# 2026-07-14 检索基线与消融

## 当前七策略消融

运行命令：

```powershell
python scripts/evaluate.py --dataset rag_eval_v1.csv --compare --neural
```

数据集包含 10 条域内问题和 2 条明确资料缺失问题。以下数字来自同一进程、同一数据快照下的 `comparison_20260714T114720Z`：

| 策略 | source_hit@5 | MRR | nDCG@5 | Recall@5 | Judge accuracy | P50 ms |
|---|---:|---:|---:|---:|---:|---:|
| BM25 | 0.9000 | 0.6283 | 0.6992 | 0.6833 | 1.0000 | 12.18 |
| 本地 LSA Embedding | 0.9000 | 0.7700 | 0.8447 | 0.7000 | 1.0000 | 18.18 |
| RRF Hybrid | 0.9000 | 0.7333 | 0.7861 | 0.7500 | 1.0000 | 18.26 |
| Hybrid + 特征精排 | 1.0000 | 0.7200 | 0.9270 | 0.8000 | 1.0000 | 31.40 |
| BGE Embedding | 0.8000 | 0.7333 | 0.7264 | 0.5833 | 0.9167 | 42.11 |
| BM25 + BGE | 1.0000 | 0.7867 | 0.8205 | 0.8333 | 1.0000 | 45.92 |
| BM25 + BGE + Cross-Encoder | 1.0000 | 0.8167 | 0.9000 | 0.8000 | 1.0000 | 3270.73 |

便携默认仍选择 Hybrid + 特征精排，因为它不需要模型缓存且 nDCG@5 最高。当前机器的在线增强档选择 BM25 + BGE：其 Recall@5 最高，P50 仍低于 50 ms。Cross-Encoder 的 MRR 最高，但 CPU P50 约 3.27 秒，只进入高质量档。单独使用 BGE Embedding 在本数据集上退化，因此不作为任何启动档位。12 条数据仍然太小，不能视为生产准确率。

神经配置固定 `BAAI/bge-small-zh@1d2363c...` 与 `BAAI/bge-reranker-base@2cfc18c...`，仅使用本地模型缓存。`prepare_ms`、完整版本号和逐题结果保存在原始 JSON 报告中。

## 历史导入基线

下表数字由旧评测集 `min_eval_active_2026-04-09.csv` 产生，用于证明完整二进制资料导入的影响。旧路径标签来自历史项目，不再作为当前排序金标。

| 指标 | 仅 TXT/MD（10 chunks） | 完整资料（462 chunks） |
|---|---:|---:|
| retrieval_nonempty_rate | 1.0000 | 1.0000 |
| evidence_pass_rate | 1.0000 | 1.0000 |
| source_hit_at_5 | 0.0000 | 0.5556 |
| expected_point_overlap_rate | 1.0000 | 1.0000 |
| latency_p50_ms | 6.32 | 165.24 |
| latency_p95_ms | 6.38 | 172.20 |

对应运行：

- `eval_20260714T105432Z`：仅导入 5 份纯文本资料，共 10 chunks。
- `eval_20260714T105533Z`：完整解析后共 462 chunks。

## 观察

- 完整导入使旧评测集的预期来源命中率从 0 提高到 0.5556，说明 PDF/DOCX 是必要知识来源。
- 检索延迟随候选片段数量显著增加；当前实现每次查询都从 SQLite 加载并计算文档频率，后续需要内存索引或专用检索服务。
- 旧版 `evidence_pass_rate` 与 `expected_point_overlap_rate` 区分度不足，因此 0.2.0 已改为 Evidence Judge v2 和版本化当前项目标题。

原始机器可读报告位于 `reports/`，包含逐题来源、覆盖率和延迟。
