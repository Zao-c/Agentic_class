import hashlib
import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_public_candidate_summary_is_auditable_without_publishing_content():
    summary_path = (
        PROJECT_ROOT / "data" / "datasets" / "candidate-course-qa-summary-v1.json"
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    schema = json.loads(
        (PROJECT_ROOT / "data" / "datasets" / "candidate-summary-schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert set(schema["required"]) == set(summary)
    assert summary["total"] == 132
    assert summary["counts"] == {
        "judgement": 51,
        "single_choice": 63,
        "training_task": 18,
    }
    assert summary["source_count"] == summary["source_file_count"] == 3
    assert summary["review_status_counts"] == {"needs_teacher_review": 132}
    assert summary["duplicate_group_count"] == 10
    assert summary["duplicate_item_count"] == 20
    assert summary["duplicate_rate"] == pytest.approx(0.1515)
    assert summary["content_published"] is False
    assert summary["teacher_reviewed"] is False
    assert summary["metric_eligibility"] is False
    assert len(summary["dataset_sha256"]) == 64
    assert sum(summary["counts"].values()) == summary["total"]
    assert sum(summary["review_status_counts"].values()) == summary["total"]


def test_candidate_dataset_is_source_grounded_but_not_metric_eligible():
    root = PROJECT_ROOT / "data" / "datasets" / "candidate"
    if not (root / "course_qa_v1_manifest.json").is_file():
        pytest.skip("private candidate dataset is intentionally absent from public clone")
    manifest = json.loads((root / "course_qa_v1_manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"] == {
        "judgement": 51,
        "single_choice": 63,
        "training_task": 18,
    }
    assert manifest["total"] == 132
    assert manifest["metric_eligibility"] is False
    items = [
        json.loads(line)
        for line in (root / "course_qa_v1.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(items) == 132
    public_summary = json.loads(
        (
            PROJECT_ROOT
            / "data"
            / "datasets"
            / "candidate-course-qa-summary-v1.json"
        ).read_text(encoding="utf-8")
    )
    assert hashlib.sha256((root / "course_qa_v1.jsonl").read_bytes()).hexdigest() == (
        public_summary["dataset_sha256"]
    )
    assert len({item["item_id"] for item in items}) == 132
    assert all(item["dataset"]["tier"] == "candidate" for item in items)
    assert all(item["review"]["status"] == "needs_teacher_review" for item in items)
    assert all(len(item["source"]["sha256"]) == 64 for item in items)
