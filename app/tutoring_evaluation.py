import json
import statistics
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import Settings
from app.retrieval import Retriever
from app.runtime_dirs import isolated_directory
from app.storage import Store
from app.tutoring import TutoringService


class TutoringEvaluationService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _resolve_dataset(self, requested: Optional[str]) -> Path:
        candidate = (
            self.settings.evaluation_root / (requested or "tutoring_eval_v1.json")
        ).resolve()
        root = self.settings.evaluation_root.resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError("辅导评测文件必须位于 EVALUATION_ROOT 内")
        if not candidate.exists():
            raise FileNotFoundError(str(candidate))
        return candidate

    def run(
        self, dataset_path: Optional[str] = None, limit: Optional[int] = None
    ) -> Dict[str, Any]:
        path = self._resolve_dataset(dataset_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = payload["cases"][:limit] if limit else payload["cases"]
        if not cases:
            raise ValueError("辅导评测集没有有效用例")
        with isolated_directory(
            self.settings.database_path.parent / "evaluations", "tutoring-"
        ) as temporary:
            eval_settings = replace(
                self.settings,
                database_path=temporary / "evaluation.db",
                retrieval_strategy="hybrid_rerank",
                auto_ingest=False,
                auto_ingest_alarm_codes=False,
                auto_ingest_knowledge_points=False,
            )
            store = Store(eval_settings.database_path)
            retriever = Retriever(store, eval_settings)
            retriever.import_directory(eval_settings.knowledge_root, include_binary=False)
            tutoring = TutoringService(store, retriever)
            tutoring.import_file(eval_settings.knowledge_point_data_path)
            details = [self._run_case(case, tutoring, store) for case in cases]

        count = len(details)
        metrics = {
            "case_count": count,
            "score_range_accuracy": self._mean(details, "score_in_range"),
            "mastery_status_accuracy": self._mean(details, "mastery_status_correct"),
            "source_traceability_rate": self._mean(details, "source_traceable"),
            "progress_update_rate": self._mean(details, "progress_updated"),
            "average_absolute_score_error": round(
                statistics.mean(item["absolute_score_error"] for item in details), 2
            ),
        }
        run_at = datetime.now(timezone.utc)
        run_id = "tutoring_eval_%s" % run_at.strftime("%Y%m%dT%H%M%SZ")
        report = {
            "schema_version": "1.0.0",
            "evaluation_run_id": run_id,
            "dataset": path.name,
            "dataset_version": payload.get("version"),
            "configuration": {
                "grading": "deterministic_keyword_criteria_v1",
                "mastery": "cumulative_mean_v1",
                "retrieval_strategy": "hybrid_rerank",
                "knowledge_point_catalog": self.settings.knowledge_point_data_path.name,
            },
            "metrics": metrics,
            "cases": details,
            "created_at": run_at.isoformat(),
        }
        self.settings.reports_root.mkdir(parents=True, exist_ok=True)
        json_path = self.settings.reports_root / (run_id + ".json")
        md_path = self.settings.reports_root / (run_id + ".md")
        json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        markdown = [
            "# 个性化辅导闭环评测",
            "",
            "- 运行 ID：`%s`" % run_id,
            "- 数据集：`%s`" % path.name,
            "",
            "| 指标 | 实测值 |",
            "|---|---:|",
        ]
        markdown.extend("| %s | %s |" % item for item in metrics.items())
        markdown.extend(
            ["", "> 练习生成、批改和掌握度更新均在隔离 SQLite 中端到端执行。"]
        )
        md_path.write_text("\n".join(markdown), encoding="utf-8")
        report["report_files"] = [str(json_path), str(md_path)]
        return report

    @staticmethod
    def _run_case(
        case: Dict[str, Any], tutoring: TutoringService, store: Store
    ) -> Dict[str, Any]:
        user_id = "eval-%s" % case["id"]
        exercise = tutoring.generate_exercise(
            user_id, knowledge_point_id=case["knowledge_point_id"]
        )
        graded = tutoring.grade_answer(exercise["exercise_id"], user_id, case["answer"])
        progress = next(
            item
            for item in store.student_progress(user_id)
            if item["knowledge_point_id"] == case["knowledge_point_id"]
        )
        expected_score = float(case["expected_score"])
        return {
            "id": case["id"],
            "knowledge_point_id": case["knowledge_point_id"],
            "score": graded["score"],
            "expected_score": expected_score,
            "absolute_score_error": abs(graded["score"] - expected_score),
            "score_in_range": (
                float(case["score_min"]) <= graded["score"] <= float(case["score_max"])
            ),
            "mastery_status": graded["mastery"]["status"],
            "expected_mastery_status": case["expected_mastery_status"],
            "mastery_status_correct": (
                graded["mastery"]["status"] == case["expected_mastery_status"]
            ),
            "matched_points": graded["matched_points"],
            "missing_points": graded["missing_points"],
            "source_traceable": bool(exercise["citation"].get("chunk_id")),
            "progress_updated": (
                progress["attempts"] == 1
                and progress["mastery_score"] == graded["score"]
            ),
        }

    @staticmethod
    def _mean(items: List[Dict[str, Any]], key: str) -> float:
        return round(sum(bool(item[key]) for item in items) / len(items), 4)

