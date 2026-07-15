import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
    assert len({item["item_id"] for item in items}) == 132
    assert all(item["dataset"]["tier"] == "candidate" for item in items)
    assert all(item["review"]["status"] == "needs_teacher_review" for item in items)
    assert all(len(item["source"]["sha256"]) == 64 for item in items)
