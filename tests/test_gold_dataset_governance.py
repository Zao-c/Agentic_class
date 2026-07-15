import csv
import json
from pathlib import Path

import pytest

from scripts.manage_gold_dataset import (
    GovernanceError,
    REVIEW_COLUMNS,
    freeze_gold,
    import_reviews,
    sha256_file,
    validate_audit,
    write_review_template,
)


def _candidate(item_id: str, source_path: Path, project_root: Path) -> dict:
    return {
        "item_id": item_id,
        "task_type": "knowledge_qa",
        "subtype": "concept",
        "input_turns": [{"role": "student", "content": f"question {item_id}"}],
        "reference_answer": f"answer {item_id}",
        "required_slots": [],
        "expected_status": "completed",
        "expected_tools": ["course_retrieval"],
        "risk_level": "low",
        "source": {
            "relative_path": source_path.relative_to(project_root).as_posix(),
            "sha256": sha256_file(source_path),
            "locator": "page:1",
            "excerpt": "source excerpt",
            "source_type": "teacher_material",
        },
        "provenance_type": "source_extracted",
        "review": {
            "status": "needs_teacher_review",
            "reviewer_id": None,
            "reviewed_at": None,
            "decision": None,
        },
        "privacy": {
            "status": "checked",
            "deidentification_method": "not_applicable",
            "deletion_status": "active",
        },
        "dataset": {
            "tier": "candidate",
            "version": "1.0.0",
            "split": "unassigned",
            "dedup_group": item_id,
            "leakage_group": "manual-a",
        },
    }


def _workspace(tmp_path: Path, item_ids=("qa-1", "qa-2", "qa-3")):
    project_root = tmp_path / "project"
    source = project_root / "sources" / "manual.txt"
    source.parent.mkdir(parents=True)
    source.write_text("teacher controlled source", encoding="utf-8")
    candidate_path = project_root / "candidate.jsonl"
    items = [_candidate(item_id, source, project_root) for item_id in item_ids]
    candidate_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items),
        encoding="utf-8",
    )
    return project_root, candidate_path


def _row(item_id: str, decision: str = "accepted") -> dict[str, str]:
    row = {field: "" for field in REVIEW_COLUMNS}
    row.update(
        {
            "item_id": item_id,
            "review_decision": decision,
            "reviewer_id": "teacher-007",
            "reviewer_role": "teacher",
            "reviewed_at": "2026-07-15T08:30:00+08:00",
            "human_review_attestation": "true",
            "source_verified": "true",
            "privacy_checked": "true",
            "safety_checked": "true",
            "split": "test" if decision == "accepted" else "",
            "review_note": "outdated wording" if decision == "rejected" else "confirmed",
        }
    )
    return row


def _write_reviews(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def test_review_template_has_explicit_human_and_gate_fields(tmp_path):
    _, candidate_path = _workspace(tmp_path, ("qa-1",))
    template = tmp_path / "review.csv"
    write_review_template(candidate_path, template)
    with template.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["review_decision"] == ""
    assert rows[0]["human_review_attestation"] == ""
    assert rows[0]["source_verified"] == ""


def test_import_keeps_unreviewed_out_and_requires_human_teacher_attestation(tmp_path):
    _, candidate_path = _workspace(tmp_path)
    review_csv = tmp_path / "review.csv"
    pending = {field: "" for field in REVIEW_COLUMNS}
    pending["item_id"] = "qa-3"
    _write_reviews(review_csv, [_row("qa-1"), _row("qa-2", "rejected"), pending])
    audit_path = tmp_path / "audit.json"
    audit = import_reviews(candidate_path, review_csv, audit_path, "batch-001")
    assert audit["counts"] == {"accepted": 1, "rejected": 1, "pending": 1}
    assert audit["review_input"]["sha256"] == sha256_file(review_csv)
    assert [record["item_id"] for record in audit["records"]] == ["qa-1", "qa-2"]
    assert all(record["automation_decision"] is False for record in audit["records"])

    invalid = _row("qa-1")
    invalid["human_review_attestation"] = "false"
    _write_reviews(review_csv, [invalid])
    with pytest.raises(GovernanceError, match="human teacher attestation"):
        import_reviews(candidate_path, review_csv, audit_path, "batch-002")


def test_freeze_writes_only_accepted_with_versioned_hash_manifest(tmp_path):
    project_root, candidate_path = _workspace(tmp_path)
    review_csv = tmp_path / "review.csv"
    pending = {field: "" for field in REVIEW_COLUMNS}
    pending["item_id"] = "qa-3"
    accepted = _row("qa-1")
    accepted["reviewed_question"] = "teacher corrected question"
    _write_reviews(review_csv, [accepted, _row("qa-2", "rejected"), pending])
    audit_path = tmp_path / "audit.json"
    import_reviews(candidate_path, review_csv, audit_path, "batch-gold-001")
    gold_root = tmp_path / "gold"
    rejected_root = tmp_path / "rejected"
    manifest = freeze_gold(
        candidate_path,
        audit_path,
        "1.0.0",
        gold_root,
        rejected_root,
        project_root=project_root,
    )
    gold_path = gold_root / "course_qa_v1.0.0.jsonl"
    gold_items = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines()]
    rejected_items = [
        json.loads(line)
        for line in (rejected_root / "course_qa_v1.0.0.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [item["item_id"] for item in gold_items] == ["qa-1"]
    assert gold_items[0]["input_turns"][0]["content"] == "teacher corrected question"
    assert [item["item_id"] for item in rejected_items] == ["qa-2"]
    assert manifest["item_count"] == 1
    assert manifest["counts"]["pending"] == 1
    assert manifest["artifact"]["sha256"] == sha256_file(gold_path)
    assert manifest["provenance"]["review_audit_sha256"] == sha256_file(audit_path)
    assert manifest["provenance"]["review_input_sha256"]
    assert manifest["policy"]["unreviewed_items_included"] is False

    with pytest.raises(GovernanceError, match="already exists"):
        freeze_gold(
            candidate_path,
            audit_path,
            "1.0.0",
            gold_root,
            rejected_root,
            project_root=project_root,
        )


def test_freeze_refuses_candidate_or_audit_tampering(tmp_path):
    project_root, candidate_path = _workspace(tmp_path, ("qa-1",))
    review_csv = tmp_path / "review.csv"
    _write_reviews(review_csv, [_row("qa-1")])
    audit_path = tmp_path / "audit.json"
    import_reviews(candidate_path, review_csv, audit_path, "batch-tamper")
    candidate_path.write_text(candidate_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(GovernanceError, match="snapshot hash"):
        validate_audit(candidate_path, audit_path)

    # Restore the reviewed bytes, then prove direct audit manipulation is rejected.
    candidate_path.write_text(candidate_path.read_text(encoding="utf-8").rstrip() + "\n", encoding="utf-8")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["records"][0]["automation_decision"] = True
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(GovernanceError, match="automated decisions"):
        freeze_gold(
            candidate_path,
            audit_path,
            "1.0.0",
            tmp_path / "gold",
            tmp_path / "rejected",
            project_root=project_root,
        )

    audit["records"][0]["automation_decision"] = False
    audit["records"][0]["checks"] = {}
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(GovernanceError, match="checks are incomplete"):
        validate_audit(candidate_path, audit_path)


def test_freeze_creates_no_gold_when_every_item_is_pending_or_rejected(tmp_path):
    project_root, candidate_path = _workspace(tmp_path, ("qa-1", "qa-2"))
    review_csv = tmp_path / "review.csv"
    pending = {field: "" for field in REVIEW_COLUMNS}
    pending["item_id"] = "qa-2"
    _write_reviews(review_csv, [_row("qa-1", "rejected"), pending])
    audit_path = tmp_path / "audit.json"
    import_reviews(candidate_path, review_csv, audit_path, "batch-no-gold")
    gold_root = tmp_path / "gold"
    with pytest.raises(GovernanceError, match="no explicitly teacher-accepted"):
        freeze_gold(
            candidate_path,
            audit_path,
            "1.0.0",
            gold_root,
            tmp_path / "rejected",
            project_root=project_root,
        )
    assert not gold_root.exists()
