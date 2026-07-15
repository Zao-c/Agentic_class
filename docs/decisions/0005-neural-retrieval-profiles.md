# ADR-0005：神经检索作为显式可选档位

- 状态：已采纳
- 日期：2026-07-14
- 补充：ADR-0004

## 决策

保留 `hybrid_rerank` 作为便携默认，并提供两个显式神经档位：

```text
portable       = BM25 + 本地 LSA + feature_reranker_v1
neural-online  = BM25 + BGE Embedding
neural-quality = BM25 + BGE Embedding + BGE Cross-Encoder
```

模型必须记录仓库 ID 与提交版本，默认 `local_files_only=true`。课程向量按模型版本与片段指纹落盘缓存；模型缓存不存在或不完整时，神经档启动失败并给出明确错误，不自动回退或下载。

## 实测依据

在 `rag_eval_v1.csv` 的 12 条样本上：

- `hybrid_rerank` 的 nDCG@5 为 0.927，P50 为 31.40 ms，无模型依赖。
- `neural_hybrid` 的 Recall@5 为 0.8333、MRR 为 0.7867，P50 为 45.92 ms。
- `neural_hybrid_rerank` 的 MRR 为 0.8167，但 CPU P50 为 3270.73 ms。
- 单独 `neural_embedding` 的 source_hit@5 为 0.8、Recall@5 为 0.5833，未证明可替代混合召回。

完整报告：`reports/comparison_20260714T114720Z.json`。

## 结果

- 默认部署不需要 Hugging Face 模型或神经依赖。
- 当前开发机可用 `neural-online` 获得更高的首条与来源召回指标。
- Cross-Encoder 的质量收益与延迟代价被隔离到高质量档，避免误设为在线默认。
- 模型缓存是神经档的显式部署前置条件，需单独纳入容器和离线环境交付流程。
