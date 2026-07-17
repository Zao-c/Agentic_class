from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from scripts.build_review_package import (
    PROJECT_ROOT,
    ReviewPackageError,
    build_package,
    validate_package,
)
from scripts.manage_gold_dataset import GovernanceError, validate_audit


ALARMS_PATH = PROJECT_ROOT / "data/structured/alarm_codes_v1.json"
REGISTRY_PATH = PROJECT_ROOT / "data/sources/abb_irb120_irc5_registry_v1.json"
SUMMARY_PATH = PROJECT_ROOT / "data/datasets/simulated-review-package-summary-v1.json"


def _candidate(item_id: str) -> dict:
    return {
        "item_id": item_id,
        "task_type": "knowledge_qa",
        "subtype": "concept",
        "input_turns": [{"role": "student", "content": f"question {item_id}"}],
        "reference_answer": f"answer {item_id}",
        "source": {
            "relative_path": "sources/manual.md",
            "sha256": "a" * 64,
            "locator": "page:1",
        },
        "review": {"status": "needs_teacher_review"},
        "dataset": {"tier": "candidate"},
    }


def _build(tmp_path: Path) -> tuple[dict, Path]:
    root = tmp_path / "project"
    candidate_path = root / "data/datasets/candidate/course_qa_v1.jsonl"
    alarms_path = root / "data/structured/alarm_codes_v1.json"
    registry_path = root / "data/sources/abb_irb120_irc5_registry_v1.json"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    alarms_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    candidates = [_candidate("qa-1"), _candidate("qa-2")]
    candidate_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in candidates),
        encoding="utf-8",
    )
    alarms_path.write_bytes(ALARMS_PATH.read_bytes())
    registry_path.write_bytes(REGISTRY_PATH.read_bytes())
    package = build_package(
        candidate_path=candidate_path,
        alarms_path=alarms_path,
        registry_path=registry_path,
        package_id="simulated-review-v1",
        generated_at="2026-07-17T00:00:00Z",
        project_root=root,
    )
    return package, candidate_path


def test_package_mixes_qa_and_alarm_records_without_claiming_teacher_or_gold(tmp_path):
    package, _ = _build(tmp_path)

    assert package["artifact_type"] == "simulated_review_package"
    assert package["review_mode"] == "simulation"
    assert package["teacher_reviewed"] is False
    assert package["human_review_attestation"] is False
    assert package["metric_eligibility"] is False
    assert package["gold_freeze_eligible"] is False
    assert package["item_type_counts"] == {"alarm_code": 29, "course_qa": 2}
    assert package["status_counts"] == {"simulated_review": 31}
    assert all(record["review_status"] == "simulated_review" for record in package["records"])
    assert all(record["review_authority"] == "simulation" for record in package["records"])
    assert all(
        not {"reviewer_id", "reviewer_role", "reviewed_at"}.intersection(record)
        for record in package["records"]
    )


def test_package_keeps_canonical_item_hashes_and_registered_alarm_evidence(tmp_path):
    package, candidate_path = _build(tmp_path)
    records = {record["item_id"]: record for record in package["records"]}
    candidate = json.loads(candidate_path.read_text(encoding="utf-8").splitlines()[0])
    expected_hash = hashlib.sha256(
        json.dumps(
            candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    assert records[candidate["item_id"]]["source_item_sha256"] == expected_hash

    alarm_records = [record for record in package["records"] if record["item_type"] == "alarm_code"]
    assert len(alarm_records) == 29
    assert all(record["source_evidence"]["official_url"].startswith("https://library.e.abb.com/public/") for record in alarm_records)
    assert all(record["suggested_checks"]["printed_page_registered"] for record in alarm_records)


def test_validation_rejects_teacher_impersonation_or_gold_eligibility(tmp_path):
    package, _ = _build(tmp_path)
    package["records"][0]["reviewer_role"] = "teacher"
    with pytest.raises(ReviewPackageError, match="teacher identity"):
        validate_package(package)

    package, _ = _build(tmp_path)
    package["gold_freeze_eligible"] = True
    with pytest.raises(ReviewPackageError, match="cannot claim"):
        validate_package(package)


def test_gold_validator_refuses_simulated_package(tmp_path: Path):
    package, candidate_path = _build(tmp_path)
    package_path = tmp_path / "package.json"
    package_path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(GovernanceError, match="teacher_review_audit"):
        validate_audit(candidate_path, package_path)


def test_public_summary_is_content_free_relative_and_matches_tracked_artifact():
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))

    assert summary["artifact_type"] == "simulated_review_package_summary"
    assert summary["source_artifact_type"] == "simulated_review_package"
    assert summary["content_included"] is False
    assert summary["item_type_counts"] == {"alarm_code": 29, "course_qa": 132}
    assert summary["status_counts"] == {"simulated_review": 161}
    rendered = json.dumps(summary, ensure_ascii=False)
    assert "candidate_payload" not in rendered
    assert re.search(r"[A-Za-z]:\\\\", rendered) is None
    assert all(not item["relative_path"].startswith(("/", "\\")) for item in summary["source_snapshots"])
