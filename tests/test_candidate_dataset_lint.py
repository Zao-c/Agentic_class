import json
from copy import deepcopy

import pytest

from scripts.lint_candidate_dataset import build_public_summary, lint_items


def _candidate(
    item_id: str,
    *,
    subtype: str = "judgement",
    source_path: str = "data/active/question_bank/private-a.docx",
    dedup_group: str | None = None,
    leakage_group: str = "private-a.docx",
    split: str = "unassigned",
) -> dict:
    return {
        "item_id": item_id,
        "task_type": "knowledge_qa",
        "subtype": subtype,
        "input_turns": [
            {"role": "student", "content": f"PRIVATE QUESTION {item_id}"}
        ],
        "reference_answer": f"PRIVATE ANSWER {item_id}",
        "grading_criteria": [],
        "required_slots": [],
        "expected_status": "completed",
        "expected_tools": ["course_retrieval"],
        "risk_level": "low",
        "equipment_scope": {
            "brand": "ABB",
            "model": None,
            "controller": None,
            "software_version": None,
            "scope_status": "course_context_only",
        },
        "source": {
            "relative_path": source_path,
            "sha256": "a" * 64,
            "locator": "paragraph:1;question:1",
            "excerpt": f"PRIVATE EXCERPT {item_id}",
            "source_type": "course_question_bank",
        },
        "provenance_type": "source_extracted",
        "review": {
            "status": "needs_teacher_review",
            "reviewer_id": None,
            "reviewed_at": None,
            "decision": None,
            "note": None,
        },
        "privacy": {
            "status": "automated_no_personal_data_detected",
            "deidentification_method": "not_applicable",
            "retention_until": None,
            "deletion_status": "active",
        },
        "dataset": {
            "tier": "candidate",
            "version": "1.0.0",
            "split": split,
            "dedup_group": dedup_group or item_id,
            "leakage_group": leakage_group,
        },
    }


def _codes(report: dict, severity: str) -> set[str]:
    return {issue["code"] for issue in report[severity]}


def test_lint_accepts_unique_complete_candidate_items():
    report = lint_items([_candidate("qa-1"), _candidate("qa-2")])

    assert report["errors"] == []
    assert report["warnings"] == []


def test_lint_reports_duplicate_ids_and_missing_required_fields_as_errors():
    first = _candidate("qa-duplicate")
    duplicate = deepcopy(first)
    incomplete = _candidate("qa-incomplete")
    del incomplete["source"]["sha256"]

    report = lint_items([first, duplicate, incomplete])

    assert "duplicate_item_id" in _codes(report, "errors")
    assert "missing_required_field" in _codes(report, "errors")


def test_lint_reports_duplicate_content_group_as_warning_not_error():
    items = [
        _candidate("qa-1", dedup_group="same-normalized-question"),
        _candidate("qa-2", dedup_group="same-normalized-question"),
    ]

    report = lint_items(items)

    assert "duplicate_group" in _codes(report, "warnings")
    assert "duplicate_group" not in _codes(report, "errors")


@pytest.mark.parametrize(
    ("group_field", "expected_code"),
    [
        ("dedup_group", "dedup_group_split_leakage"),
        ("leakage_group", "leakage_group_split_leakage"),
    ],
)
def test_lint_rejects_group_members_crossing_benchmark_splits(
    group_field: str, expected_code: str
):
    first = _candidate("qa-train", split="train")
    second = _candidate("qa-test", split="test")
    first["dataset"][group_field] = "shared-group"
    second["dataset"][group_field] = "shared-group"

    report = lint_items([first, second])

    assert expected_code in _codes(report, "errors")


def test_public_summary_aggregates_without_disclosing_private_content():
    items = [
        _candidate(
            "qa-j-1", subtype="judgement", dedup_group="duplicate-question"
        ),
        _candidate(
            "qa-j-2", subtype="judgement", dedup_group="duplicate-question"
        ),
        _candidate(
            "qa-sc-1",
            subtype="single_choice",
            source_path="data/active/question_bank/private-b.docx",
            leakage_group="private-b.docx",
        ),
        _candidate("qa-task-1", subtype="training_task"),
    ]
    dataset_sha256 = "f" * 64

    summary = build_public_summary(
        items,
        dataset_sha256=dataset_sha256,
        audited_on="2026-07-15",
    )

    assert summary["schema_version"] == "1.0.0"
    assert summary["audited_on"] == "2026-07-15"
    assert summary["dataset_sha256"] == dataset_sha256
    assert summary["dataset_tier"] == "candidate"
    assert summary["metric_eligibility"] is False
    assert summary["content_published"] is False
    assert summary["teacher_reviewed"] is False
    assert summary["total"] == 4
    assert summary["counts"] == {
        "judgement": 2,
        "single_choice": 1,
        "training_task": 1,
    }
    assert summary["source_count"] == 2
    assert summary["duplicate_group_count"] == 1
    assert summary["duplicate_item_count"] == 2
    assert summary["duplicate_rate"] == pytest.approx(0.5)
    assert summary["review_status_counts"] == {"needs_teacher_review": 4}
    assert "items" not in summary

    serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    for item in items:
        assert item["input_turns"][0]["content"] not in serialized
        assert item["reference_answer"] not in serialized
        assert item["source"]["relative_path"] not in serialized
        assert item["source"]["locator"] not in serialized
        assert item["source"]["excerpt"] not in serialized
