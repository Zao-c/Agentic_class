"""Re-score stored benchmark observations after a documented scoring-only protocol fix."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.agent_benchmark import (
    BENCHMARK_PROTOCOL_VERSION,
    BENCHMARK_SCHEMA_VERSION,
    BenchmarkValidationError,
    RunnerObservation,
    aggregate_runner,
    dataset_sha256,
    load_dataset,
    write_report,
)


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def normalized_text_sha256(content: bytes) -> str:
    """Hash report content consistently across Git CRLF/LF checkouts."""

    normalized = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def _experiment_status(reports: List[Dict[str, Any]]) -> str:
    rows = [case for report in reports for case in report["cases"]]
    if not reports:
        return "not_run"
    if rows and all(item["error"] is not None for item in rows):
        return "failed"
    if any(not report["comparison_eligible"] for report in reports):
        return "completed_with_ineligible_runners"
    if any(item["error"] is not None for item in rows):
        return "completed_with_errors"
    return "completed"


def rescore(source_path: Path, dataset_path: Path) -> Dict[str, Any]:
    source_bytes = source_path.read_bytes()
    source = json.loads(source_bytes.decode("utf-8"))
    dataset = load_dataset(dataset_path)
    if source.get("dataset", {}).get("id") != dataset.dataset_id:
        raise BenchmarkValidationError("source report and dataset id do not match")

    cases = {case.id: case for case in dataset.cases}
    repetitions = int(source.get("repetitions", 1))
    if repetitions < 1:
        raise BenchmarkValidationError("source report repetitions must be at least one")
    reports: List[Dict[str, Any]] = []
    for raw_runner in source.get("runner_reports", []):
        observations = [
            RunnerObservation.model_validate(
                {
                    key: value
                    for key, value in raw_case.items()
                    if key not in {"scores", "total_tokens"}
                }
            )
            for raw_case in raw_runner.get("cases", [])
        ]
        observed_keys = [(item.case_id, item.repetition) for item in observations]
        expected_keys = [
            (case_id, repetition)
            for repetition in range(1, repetitions + 1)
            for case_id in cases
        ]
        if (
            len(observed_keys) != len(set(observed_keys))
            or set(observed_keys) != set(expected_keys)
        ):
            raise BenchmarkValidationError(
                "%s observations do not match the complete case/repetition matrix"
                % raw_runner.get("runner", "unknown")
            )
        reports.append(
            aggregate_runner(raw_runner["runner"], cases, observations)
        )

    rescored = dict(source)
    rescored.update(
        {
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            "protocol_version": BENCHMARK_PROTOCOL_VERSION,
            "evaluation_run_id": source["evaluation_run_id"] + "_rescored_v2_1",
            "experiment_status": _experiment_status(reports),
            "dataset": {
                "id": dataset.dataset_id,
                "version": dataset.version,
                "status": dataset.status,
                "teacher_reviewed": dataset.teacher_reviewed,
                "data_origin": dataset.data_origin,
                "actor_mode": dataset.actor_mode,
                "label_authority": dataset.label_authority,
                "metric_eligibility": dataset.metric_eligibility,
                "formal_comparison_eligible": dataset.formal_comparison_eligible,
                "sha256": dataset_sha256(dataset_path),
                "case_count": len(dataset.cases),
            },
            "runner_reports": reports,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "rescoring": {
                "source_report": source_path.name,
                "source_report_sha256": normalized_text_sha256(source_bytes),
                "source_report_hash_normalization": "crlf_and_cr_to_lf",
                "source_protocol_version": source.get("protocol_version"),
                "llm_reexecuted": False,
                "changes": [
                    "canonicalize documented free-agent slot aliases for shared scoring",
                    "score proposed tools separately from executed control-plane tools",
                    "compute unauthorized-tool block rate over all visible turns",
                ],
            },
        }
    )
    return rescored


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-score raw Agent benchmark observations without new model calls"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = _resolve(args.source)
    dataset = _resolve(args.dataset)
    output = _resolve(args.output)
    report = rescore(source, dataset)
    write_report(report, output)
    print(
        json.dumps(
            {
                "report": str(output.relative_to(PROJECT_ROOT)),
                "protocol_version": report["protocol_version"],
                "llm_reexecuted": False,
                "metrics": {
                    item["runner"]: item["metrics"]
                    for item in report["runner_reports"]
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
