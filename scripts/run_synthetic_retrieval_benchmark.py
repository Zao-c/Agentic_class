"""Run the public synthetic retrieval benchmark in an isolated local store.

The published output contains aggregate engineering metrics only. It deliberately
excludes raw questions, local absolute paths, and any claim of teacher review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import PROJECT_ROOT, Settings
from app.evaluation import EvaluationService
from app.retrieval import Retriever
from app.storage import Store


DATASET_NAME = "rag_synthetic_180_v1.csv"
MANIFEST_NAME = "rag_synthetic_180_v1_manifest.json"
CORPUS_RELATIVE = Path("data/public_sample/synthetic_classroom_v1")
PUBLIC_REPORT_RELATIVE = Path("reports/rag_synthetic_180_local4_v1.json")
LOCAL_STRATEGIES = ("bm25", "embedding", "hybrid", "hybrid_rerank")
NEURAL_STRATEGIES = (
    "neural_embedding",
    "neural_hybrid",
    "neural_hybrid_rerank",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_manifest(root: Path) -> dict[str, Any]:
    path = root / "data/eval" / MANIFEST_NAME
    manifest = json.loads(path.read_text(encoding="utf-8"))
    dataset = root / "data/eval" / DATASET_NAME
    if _sha256(dataset) != manifest["artifacts"]["dataset_sha256"]:
        raise ValueError("synthetic dataset hash does not match its manifest")
    if manifest.get("formal_comparison_eligible") is not False:
        raise ValueError("synthetic dataset must not be formal-comparison eligible")
    if manifest.get("teacher_reviewed") is not False:
        raise ValueError("synthetic dataset must not claim teacher review")
    return manifest


def _build_settings(root: Path, work: Path, include_neural: bool) -> Settings:
    return Settings(
        database_path=work / "synthetic_retrieval.db",
        knowledge_root=root / CORPUS_RELATIVE,
        evaluation_root=root / "data/eval",
        reports_root=work / "raw_reports",
        neural_index_cache_root=work / "neural_indexes",
        neural_local_files_only=True,
        neural_device="cpu",
        retrieval_top_k=5,
        retrieval_strategy="hybrid_rerank",
        auto_ingest=False,
        auto_ingest_alarm_codes=False,
        auto_ingest_knowledge_points=False,
    )


def _public_report(
    manifest: dict[str, Any],
    corpus_root: Path,
    ingestion: dict[str, Any],
    reports: list[dict[str, Any]],
    include_neural: bool,
) -> dict[str, Any]:
    source_hashes = {
        path.name: _sha256(path)
        for path in sorted(corpus_root.glob("*.md"))
    }
    if source_hashes != manifest["artifacts"]["source_sha256"]:
        raise ValueError("synthetic corpus hashes do not match the dataset manifest")
    return {
        "schema_version": "1.0.0",
        "report_id": "rag_synthetic_180_local7_v1" if include_neural else "rag_synthetic_180_local4_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "synthetic_engineering_validation",
        "dataset": {
            "dataset_id": manifest["dataset_id"],
            "version": manifest["version"],
            "case_count": manifest["case_count"],
            "family_count": manifest["family_count"],
            "split_case_counts": manifest["split_case_counts"],
            "dataset_sha256": manifest["artifacts"]["dataset_sha256"],
            "teacher_reviewed": False,
            "formal_comparison_eligible": False,
            "metric_eligibility": "synthetic_engineering_only",
        },
        "corpus": {
            "content_origin": "synthetic_public_original",
            "document_count": len(source_hashes),
            "document_sha256": source_hashes,
            "ingestion": {
                "created": ingestion["created"],
                "duplicate": ingestion["duplicate"],
                "failed": ingestion["failed"],
            },
        },
        "protocol": {
            "top_k": 5,
            "isolated_store": True,
            "neural_local_files_only": True,
            "strategies": [report["configuration"]["retriever"] for report in reports],
            "runtime": {
                "python": platform.python_version(),
                "platform": platform.system(),
            },
        },
        "results": {
            report["configuration"]["retriever"]: report["metrics"] for report in reports
        },
        "claim_boundary": manifest["claim_boundary"],
        "publication_note": (
            "Aggregate results are engineering evidence over deterministic simulated student "
            "questions and original synthetic documents. They are not Gold metrics and do not "
            "measure real-course or production accuracy."
        ),
    }


def run(root: Path = PROJECT_ROOT, include_neural: bool = False) -> tuple[dict[str, Any], Path]:
    root = Path(root).resolve()
    manifest = _load_manifest(root)
    runtime_root = root / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="synthetic-rag-", dir=runtime_root) as temporary:
        work = Path(temporary)
        settings = _build_settings(root, work, include_neural)
        settings.ensure_directories()
        store = Store(settings.database_path)
        retriever = Retriever(store, settings)
        ingestion = retriever.import_directory(settings.knowledge_root, include_binary=False)
        if ingestion["failed"] or ingestion["created"] != manifest["source_document_count"]:
            raise RuntimeError(f"synthetic corpus ingestion failed: {ingestion}")
        evaluator = EvaluationService(
            retriever,
            settings.evaluation_root,
            settings.reports_root,
            settings.evidence_threshold,
        )
        strategies = list(LOCAL_STRATEGIES)
        if include_neural:
            strategies.extend(NEURAL_STRATEGIES)
        reports = [evaluator.run(DATASET_NAME, strategy=strategy) for strategy in strategies]
        public = _public_report(
            manifest,
            settings.knowledge_root,
            ingestion,
            reports,
            include_neural,
        )
    output = root / (
        "reports/rag_synthetic_180_local7_v1.json"
        if include_neural
        else PUBLIC_REPORT_RELATIVE
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return public, output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the isolated public synthetic retrieval benchmark."
    )
    parser.add_argument(
        "--include-neural",
        action="store_true",
        help="Also run three version-pinned neural strategies from the local model cache.",
    )
    args = parser.parse_args()
    report, output = run(include_neural=args.include_neural)
    print(
        json.dumps(
            {
                "report_id": report["report_id"],
                "strategies": report["protocol"]["strategies"],
                "results": report["results"],
                "output": output.name,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
