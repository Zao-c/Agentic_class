import csv
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.evidence import EvidenceJudge, significant_query_tokens
from app.retrieval import Retriever, tokenize
from app.schemas import RetrievalStrategy


def _basename(value: str) -> str:
    return value.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]


class EvaluationService:
    def __init__(self, retriever: Retriever, evaluation_root: Path, reports_root: Path, threshold: float):
        self.retriever = retriever
        self.evaluation_root = Path(evaluation_root)
        self.reports_root = Path(reports_root)
        self.threshold = threshold
        self.evidence_judge = EvidenceJudge(threshold)

    def _resolve_dataset(self, requested: Optional[str]) -> Path:
        if requested:
            candidate = (self.evaluation_root / requested).resolve()
            root = self.evaluation_root.resolve()
            if candidate != root and root not in candidate.parents:
                raise ValueError("评测文件必须位于 EVALUATION_ROOT 内")
            if not candidate.exists():
                raise FileNotFoundError(str(candidate))
            return candidate
        candidates = sorted(
            path for path in self.evaluation_root.glob("*.csv") if "results" not in path.name.lower()
        )
        if not candidates:
            raise FileNotFoundError("未找到可用评测 CSV")
        current = [path for path in candidates if path.name == "rag_eval_v1.csv"]
        if current:
            return current[0]
        preferred = [path for path in candidates if path.name.startswith("min_eval_active")]
        return preferred[0] if preferred else candidates[0]

    @staticmethod
    def _source_relevance(citation_title: str, expected_sources: List[str]) -> int:
        return int(any(expected and expected in citation_title for expected in expected_sources))

    def run(
        self,
        dataset_path: Optional[str] = None,
        limit: Optional[int] = None,
        strategy: Optional[str] = None,
    ) -> Dict[str, Any]:
        path = self._resolve_dataset(dataset_path)
        selected_strategy = RetrievalStrategy(strategy or self.retriever.settings.retrieval_strategy)
        rows: List[Dict[str, str]] = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("question"):
                    rows.append(row)
        if limit:
            rows = rows[:limit]
        if not rows:
            raise ValueError("评测集没有 question 字段或有效数据")
        prepare_started = time.perf_counter()
        self.retriever.prepare(selected_strategy.value)
        prepare_ms = round((time.perf_counter() - prepare_started) * 1000, 2)

        details = []
        latencies = []
        nonempty = evidence_pass = source_hits = 0
        evidence_expectation_results: List[float] = []
        reciprocal_ranks: List[float] = []
        ndcg_values: List[float] = []
        recall_values: List[float] = []
        expected_point_coverages: List[float] = []
        for row in rows:
            started = time.perf_counter()
            citations = self.retriever.search(
                row["question"], top_k=5, strategy=selected_strategy.value
            )
            latency_ms = (time.perf_counter() - started) * 1000
            latencies.append(latency_ms)
            if citations:
                nonempty += 1
            combined = "\n".join(item.excerpt for item in citations)
            decision = self.evidence_judge.judge(row["question"], citations)
            if decision.sufficient:
                evidence_pass += 1

            if row.get("expected_sources"):
                expected_sources = [
                    item.strip() for item in row["expected_sources"].split("|") if item.strip()
                ]
            else:
                expected_sources = list(dict.fromkeys(
                    _basename(row.get(field, ""))
                    for field in ("top1_source", "top2_source", "top3_source")
                    if row.get(field)
                ))
            relevances = [self._source_relevance(citation.title, expected_sources) for citation in citations]
            source_hit = bool(expected_sources) and any(relevances)
            if source_hit:
                source_hits += 1
            if expected_sources:
                first_relevant = next((rank for rank, relevance in enumerate(relevances, 1) if relevance), None)
                reciprocal_ranks.append(1 / first_relevant if first_relevant else 0.0)
                matched_sources = {
                    expected
                    for expected in expected_sources
                    if any(expected in citation.title for citation in citations)
                }
                recall_values.append(len(matched_sources) / len(expected_sources))
                dcg = sum(relevance / math.log2(rank + 1) for rank, relevance in enumerate(relevances, 1))
                ideal_relevant = min(len(expected_sources), len(citations))
                ideal_dcg = sum(1 / math.log2(rank + 1) for rank in range(1, ideal_relevant + 1))
                ndcg_values.append(dcg / ideal_dcg if ideal_dcg else 0.0)

            expected_points = row.get("expected_points", "")
            expected_tokens = {token for token in tokenize(expected_points) if len(token) > 1}
            expected_coverage = (
                len(expected_tokens & set(tokenize(combined))) / len(expected_tokens)
                if expected_tokens
                else None
            )
            if expected_coverage is not None:
                expected_point_coverages.append(expected_coverage)
            expected_evidence = row.get("expect_evidence", "").strip().lower()
            expected_evidence_value = None
            judge_correct = None
            if expected_evidence in {"true", "1", "yes"}:
                expected_evidence_value = True
                judge_correct = decision.sufficient is True
            elif expected_evidence in {"false", "0", "no"}:
                expected_evidence_value = False
                judge_correct = decision.sufficient is False
            if judge_correct is not None:
                evidence_expectation_results.append(float(judge_correct))
            details.append(
                {
                    "id": row.get("id"),
                    "category": row.get("category"),
                    "question": row["question"],
                    "retrieval_count": len(citations),
                    "evidence_score": decision.score,
                    "evidence_coverage": decision.coverage,
                    "evidence_sufficient": decision.sufficient,
                    "evidence_reasons": decision.reasons,
                    "expected_evidence": expected_evidence_value,
                    "evidence_judge_correct": judge_correct,
                    "source_hit_at_5": source_hit if expected_sources else None,
                    "reciprocal_rank": round(reciprocal_ranks[-1], 4) if expected_sources else None,
                    "ndcg_at_5": round(ndcg_values[-1], 4) if expected_sources else None,
                    "recall_at_5": round(recall_values[-1], 4) if expected_sources else None,
                    "expected_point_coverage": round(expected_coverage, 4) if expected_coverage is not None else None,
                    "latency_ms": round(latency_ms, 2),
                    "sources": [item.title for item in citations],
                    "score_components": [item.score_components for item in citations],
                }
            )

        ordered = sorted(latencies)
        p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))
        count = len(rows)
        metrics = {
            "case_count": count,
            "retrieval_nonempty_rate": round(nonempty / count, 4),
            "evidence_pass_rate": round(evidence_pass / count, 4),
            "source_hit_at_5": round(source_hits / len(reciprocal_ranks), 4) if reciprocal_ranks else None,
            "mrr": round(statistics.mean(reciprocal_ranks), 4) if reciprocal_ranks else None,
            "ndcg_at_5": round(statistics.mean(ndcg_values), 4) if ndcg_values else None,
            "recall_at_5": round(statistics.mean(recall_values), 4) if recall_values else None,
            "expected_point_coverage": round(statistics.mean(expected_point_coverages), 4) if expected_point_coverages else None,
            "evidence_judge_accuracy": round(statistics.mean(evidence_expectation_results), 4) if evidence_expectation_results else None,
            "latency_p50_ms": round(statistics.median(latencies), 2),
            "latency_p95_ms": round(ordered[p95_index], 2),
            "prepare_ms": prepare_ms,
        }
        run_at = datetime.now(timezone.utc)
        run_id = "eval_%s_%s" % (selected_strategy.value, run_at.strftime("%Y%m%dT%H%M%SZ"))
        report = {
            "schema_version": "1.0.0",
            "evaluation_run_id": run_id,
            "dataset": path.name,
            "dataset_path": str(path),
            "configuration": {
                "retriever": selected_strategy.value,
                "top_k": 5,
                "evidence_threshold": self.threshold,
                "embedding": (
                    "%s@%s"
                    % (
                        self.retriever.settings.neural_embedding_model,
                        self.retriever.settings.neural_embedding_revision,
                    )
                    if selected_strategy
                    in {
                        RetrievalStrategy.neural_embedding,
                        RetrievalStrategy.neural_hybrid,
                        RetrievalStrategy.neural_hybrid_rerank,
                    }
                    else (
                        "local_tfidf_lsa_v1"
                        if selected_strategy != RetrievalStrategy.bm25
                        else None
                    )
                ),
                "reranker": (
                    "feature_reranker_v1"
                    if selected_strategy == RetrievalStrategy.hybrid_rerank
                    else (
                        "%s@%s"
                        % (
                            self.retriever.settings.neural_reranker_model,
                            self.retriever.settings.neural_reranker_revision,
                        )
                        if selected_strategy == RetrievalStrategy.neural_hybrid_rerank
                        else None
                    )
                ),
            },
            "metrics": metrics,
            "cases": details,
            "created_at": run_at.isoformat(),
        }
        self.reports_root.mkdir(parents=True, exist_ok=True)
        json_path = self.reports_root / (run_id + ".json")
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown = [
            "# 检索评测报告",
            "",
            "- 运行 ID：`%s`" % run_id,
            "- 数据集：`%s`" % path.name,
            "- 样本数：%d" % count,
            "",
            "| 指标 | 实测值 |",
            "|---|---:|",
        ]
        markdown.extend("| %s | %s |" % item for item in metrics.items())
        markdown.extend(
            [
                "",
                "> 所有数值由本次脚本运行产生。source_hit_at_5 仅在旧评测集给出预期来源时有意义。",
            ]
        )
        (self.reports_root / (run_id + ".md")).write_text("\n".join(markdown), encoding="utf-8")
        report["report_files"] = [str(json_path), str(self.reports_root / (run_id + ".md"))]
        return report

    def run_comparison(
        self,
        dataset_path: Optional[str] = None,
        limit: Optional[int] = None,
        include_neural: bool = False,
    ) -> Dict[str, Any]:
        strategies = [
            RetrievalStrategy.bm25,
            RetrievalStrategy.embedding,
            RetrievalStrategy.hybrid,
            RetrievalStrategy.hybrid_rerank,
        ]
        if include_neural:
            strategies.extend(
                [
                    RetrievalStrategy.neural_embedding,
                    RetrievalStrategy.neural_hybrid,
                    RetrievalStrategy.neural_hybrid_rerank,
                ]
            )
        reports = [self.run(dataset_path, limit, strategy.value) for strategy in strategies]
        created_at = datetime.now(timezone.utc)
        comparison_id = "comparison_" + created_at.strftime("%Y%m%dT%H%M%SZ")
        comparison = {
            "schema_version": "1.0.0",
            "comparison_id": comparison_id,
            "dataset": reports[0]["dataset"],
            "strategies": {
                report["configuration"]["retriever"]: report["metrics"] for report in reports
            },
            "evaluation_run_ids": [report["evaluation_run_id"] for report in reports],
            "created_at": created_at.isoformat(),
        }
        json_path = self.reports_root / (comparison_id + ".json")
        json_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
        metric_names = ["source_hit_at_5", "mrr", "ndcg_at_5", "recall_at_5", "evidence_pass_rate", "latency_p50_ms", "latency_p95_ms"]
        markdown = [
            "# RAG 检索消融对比",
            "",
            "- 对比 ID：`%s`" % comparison_id,
            "- 数据集：`%s`" % reports[0]["dataset"],
            "",
            "| 策略 | %s |" % " | ".join(metric_names),
            "|---|%s|" % "|".join("---:" for _ in metric_names),
        ]
        for report in reports:
            metrics = report["metrics"]
            markdown.append(
                "| %s | %s |"
                % (
                    report["configuration"]["retriever"],
                    " | ".join(str(metrics.get(name)) for name in metric_names),
                )
            )
        markdown.extend(
            [
                "",
                "> 所有策略使用同一数据快照、同一 Top-k 和同一 Evidence Judge。feature_reranker_v1 是可解释特征精排；neural_hybrid_rerank 使用版本锁定的真实 Cross-Encoder。",
            ]
        )
        markdown_path = self.reports_root / (comparison_id + ".md")
        markdown_path.write_text("\n".join(markdown), encoding="utf-8")
        comparison["report_files"] = [str(json_path), str(markdown_path)]
        return comparison
