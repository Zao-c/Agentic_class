"""Auditable teacher-review and immutable Gold dataset workflow.

This module never infers review decisions.  A candidate can enter Gold only
when a CSV row contains an explicit accepted decision, a teacher role and a
human-review attestation, plus the required source/privacy/safety checks.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CANDIDATE = PROJECT_ROOT / "data/datasets/candidate/course_qa_v1.jsonl"
DEFAULT_REVIEW_CSV = PROJECT_ROOT / "data/datasets/candidate/course_qa_v1_review.csv"
DEFAULT_AUDIT = PROJECT_ROOT / "data/datasets/reviews/course_qa_v1_review_audit.json"
DEFAULT_GOLD_ROOT = PROJECT_ROOT / "data/datasets/gold"
DEFAULT_REJECTED_ROOT = PROJECT_ROOT / "data/datasets/rejected"

REVIEW_COLUMNS = [
    "item_id",
    "subtype",
    "question",
    "reference_answer",
    "source_path",
    "source_locator",
    "review_decision",
    "reviewer_id",
    "reviewer_role",
    "reviewed_at",
    "human_review_attestation",
    "source_verified",
    "privacy_checked",
    "safety_checked",
    "split",
    "reviewed_question",
    "reviewed_reference_answer",
    "reviewed_source_locator",
    "review_note",
]
DECISIONS = {"accepted", "rejected"}
SPLITS = {"train", "dev", "test"}
TEACHER_ROLES = {"teacher", "course_teacher"}
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


class GovernanceError(ValueError):
    """Raised when review evidence is incomplete or inconsistent."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _audit_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GovernanceError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        if not isinstance(item, dict):
            raise GovernanceError(f"{path}:{line_number}: item must be an object")
        items.append(item)
    if not items:
        raise GovernanceError("candidate dataset is empty")
    return items


def load_candidates(path: Path) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for item in _read_jsonl(path):
        item_id = str(item.get("item_id", "")).strip()
        if not item_id:
            raise GovernanceError("candidate item is missing item_id")
        if item_id in candidates:
            raise GovernanceError(f"duplicate candidate item_id: {item_id}")
        if item.get("dataset", {}).get("tier") != "candidate":
            raise GovernanceError(f"{item_id}: dataset.tier must be candidate")
        if item.get("review", {}).get("status") != "needs_teacher_review":
            raise GovernanceError(f"{item_id}: candidate review status must be needs_teacher_review")
        if not item.get("source", {}).get("sha256"):
            raise GovernanceError(f"{item_id}: source.sha256 is required")
        candidates[item_id] = item
    return candidates


def write_review_template(candidate_path: Path, output_path: Path) -> None:
    candidates = load_candidates(candidate_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for item_id in sorted(candidates):
            item = candidates[item_id]
            writer.writerow(
                {
                    "item_id": item_id,
                    "subtype": item.get("subtype", ""),
                    "question": item.get("input_turns", [{}])[0].get("content", ""),
                    "reference_answer": json.dumps(
                        item.get("reference_answer", ""), ensure_ascii=False
                    )
                    if not isinstance(item.get("reference_answer"), str)
                    else item.get("reference_answer", ""),
                    "source_path": item["source"].get("relative_path", ""),
                    "source_locator": item["source"].get("locator", ""),
                }
            )


def _parse_bool(value: str, field: str, item_id: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise GovernanceError(f"{item_id}: {field} must be true or false")
    return normalized == "true"


def _validate_reviewed_at(value: str, item_id: str) -> str:
    raw = value.strip()
    if not raw:
        raise GovernanceError(f"{item_id}: reviewed_at is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GovernanceError(f"{item_id}: reviewed_at must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise GovernanceError(f"{item_id}: reviewed_at must include a timezone")
    return raw


def _pending_row_is_clean(row: dict[str, str]) -> bool:
    review_fields = REVIEW_COLUMNS[7:]
    return all(not (row.get(field) or "").strip() for field in review_fields)


def _validated_record(
    row: dict[str, str], candidate: dict[str, Any]
) -> dict[str, Any] | None:
    item_id = row["item_id"].strip()
    decision = row.get("review_decision", "").strip().lower()
    if not decision:
        if not _pending_row_is_clean(row):
            raise GovernanceError(f"{item_id}: pending row contains review evidence but no decision")
        return None
    if decision not in DECISIONS:
        raise GovernanceError(f"{item_id}: review_decision must be accepted or rejected")
    reviewer_id = row.get("reviewer_id", "").strip()
    if not reviewer_id:
        raise GovernanceError(f"{item_id}: reviewer_id is required")
    reviewer_role = row.get("reviewer_role", "").strip().lower()
    if reviewer_role not in TEACHER_ROLES:
        raise GovernanceError(f"{item_id}: reviewer_role must identify a teacher")
    reviewed_at = _validate_reviewed_at(row.get("reviewed_at", ""), item_id)
    attested = _parse_bool(
        row.get("human_review_attestation", ""), "human_review_attestation", item_id
    )
    if not attested:
        raise GovernanceError(f"{item_id}: human teacher attestation is required")
    checks = {
        field: _parse_bool(row.get(field, ""), field, item_id)
        for field in ("source_verified", "privacy_checked", "safety_checked")
    }
    split = row.get("split", "").strip().lower()
    note = row.get("review_note", "").strip()
    if decision == "accepted":
        if not all(checks.values()):
            raise GovernanceError(f"{item_id}: accepted item requires all review checks")
        if split not in SPLITS:
            raise GovernanceError(f"{item_id}: accepted item requires train, dev or test split")
    else:
        if split:
            raise GovernanceError(f"{item_id}: rejected item cannot have a benchmark split")
        if not note:
            raise GovernanceError(f"{item_id}: rejected item requires review_note")
    return {
        "item_id": item_id,
        "candidate_item_sha256": _canonical_sha256(candidate),
        "decision": decision,
        "review_status": (
            "teacher_accepted" if decision == "accepted" else "teacher_rejected"
        ),
        "decision_authority": "human_teacher",
        "automation_decision": False,
        "reviewer_id": reviewer_id,
        "reviewer_role": reviewer_role,
        "reviewed_at": reviewed_at,
        "human_review_attestation": True,
        "checks": checks,
        "split": split or None,
        "amendments": {
            "question": row.get("reviewed_question", "").strip() or None,
            "reference_answer": row.get("reviewed_reference_answer", "").strip() or None,
            "source_locator": row.get("reviewed_source_locator", "").strip() or None,
        },
        "note": note or None,
    }


def import_reviews(
    candidate_path: Path,
    review_csv_path: Path,
    output_path: Path,
    review_batch_id: str,
) -> dict[str, Any]:
    if not review_batch_id.strip():
        raise GovernanceError("review_batch_id is required")
    candidates = load_candidates(candidate_path)
    with review_csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = [column for column in REVIEW_COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise GovernanceError("review CSV missing columns: " + ", ".join(missing))
        rows = list(reader)
    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    pending = 0
    for row_number, row in enumerate(rows, 2):
        item_id = row.get("item_id", "").strip()
        if item_id not in candidates:
            raise GovernanceError(f"review CSV row {row_number}: unknown item_id {item_id!r}")
        if item_id in seen:
            raise GovernanceError(f"review CSV row {row_number}: duplicate item_id {item_id}")
        seen.add(item_id)
        record = _validated_record(row, candidates[item_id])
        if record is None:
            pending += 1
        else:
            records.append(record)
    pending += len(candidates) - len(seen)
    counts = Counter(record["decision"] for record in records)
    audit = {
        "schema_version": "1.0.0",
        "artifact_type": "teacher_review_audit",
        "review_batch_id": review_batch_id,
        "imported_at": _utc_now(),
        "candidate": {
            "relative_path": _audit_path(candidate_path),
            "sha256": sha256_file(candidate_path),
            "item_count": len(candidates),
        },
        "review_input": {
            "path": _audit_path(review_csv_path),
            "sha256": sha256_file(review_csv_path),
        },
        "policy": {
            "decision_authority": "human_teacher_only",
            "unreviewed_items_enter_gold": False,
        },
        "counts": {
            "accepted": counts["accepted"],
            "rejected": counts["rejected"],
            "pending": pending,
        },
        "records": sorted(records, key=lambda item: item["item_id"]),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


def validate_audit(
    candidate_path: Path, audit_path: Path
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    candidates = load_candidates(candidate_path)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit.get("schema_version") != "1.0.0":
        raise GovernanceError("unsupported review audit schema_version")
    if audit.get("artifact_type") != "teacher_review_audit":
        raise GovernanceError("artifact_type must be teacher_review_audit")
    if audit.get("candidate", {}).get("sha256") != sha256_file(candidate_path):
        raise GovernanceError("candidate snapshot hash differs from the reviewed snapshot")
    records = audit.get("records")
    if not isinstance(records, list):
        raise GovernanceError("review audit records must be a list")
    seen: set[str] = set()
    for record in records:
        item_id = record.get("item_id")
        if item_id not in candidates or item_id in seen:
            raise GovernanceError(f"invalid or duplicate audited item_id: {item_id}")
        seen.add(item_id)
        if record.get("candidate_item_sha256") != _canonical_sha256(candidates[item_id]):
            raise GovernanceError(f"{item_id}: candidate item changed after review")
        if record.get("decision") not in DECISIONS:
            raise GovernanceError(f"{item_id}: invalid audited decision")
        expected_review_status = (
            "teacher_accepted"
            if record["decision"] == "accepted"
            else "teacher_rejected"
        )
        if record.get("review_status") != expected_review_status:
            raise GovernanceError(
                f"{item_id}: audited decision and review_status are inconsistent"
            )
        if record.get("decision_authority") != "human_teacher":
            raise GovernanceError(f"{item_id}: decision authority is not human_teacher")
        if record.get("automation_decision") is not False:
            raise GovernanceError(f"{item_id}: automated decisions cannot be frozen")
        if record.get("human_review_attestation") is not True:
            raise GovernanceError(f"{item_id}: human review attestation is missing")
        if not str(record.get("reviewer_id", "")).strip():
            raise GovernanceError(f"{item_id}: audited reviewer_id is missing")
        if record.get("reviewer_role") not in TEACHER_ROLES:
            raise GovernanceError(f"{item_id}: audited reviewer role is not a teacher")
        _validate_reviewed_at(str(record.get("reviewed_at", "")), item_id)
        checks = record.get("checks")
        required_check_names = ("source_verified", "privacy_checked", "safety_checked")
        if not isinstance(checks, dict) or any(name not in checks for name in required_check_names):
            raise GovernanceError(f"{item_id}: audited review checks are incomplete")
        if any(not isinstance(checks[name], bool) for name in required_check_names):
            raise GovernanceError(f"{item_id}: audited review checks must be boolean")
        if record["decision"] == "accepted":
            if record.get("split") not in SPLITS or not all(
                checks[name] for name in required_check_names
            ):
                raise GovernanceError(f"{item_id}: accepted review gates are incomplete")
        elif record.get("split") or not str(record.get("note", "")).strip():
            raise GovernanceError(f"{item_id}: rejected review evidence is incomplete")
    return candidates, audit


def _verify_source(item: dict[str, Any], project_root: Path) -> None:
    source = item.get("source", {})
    relative_path = source.get("relative_path", "")
    source_path = (project_root / relative_path).resolve()
    try:
        source_path.relative_to(project_root.resolve())
    except ValueError as exc:
        raise GovernanceError(f"{item['item_id']}: source path escapes project root") from exc
    if not source_path.is_file():
        raise GovernanceError(f"{item['item_id']}: source file does not exist: {relative_path}")
    if sha256_file(source_path) != source.get("sha256"):
        raise GovernanceError(f"{item['item_id']}: source file hash mismatch")


def _apply_review(
    candidate: dict[str, Any], record: dict[str, Any], version: str
) -> dict[str, Any]:
    item = copy.deepcopy(candidate)
    amendments = record.get("amendments", {})
    if amendments.get("question"):
        item["input_turns"][0]["content"] = amendments["question"]
    if amendments.get("reference_answer"):
        item["reference_answer"] = amendments["reference_answer"]
    if amendments.get("source_locator"):
        item["source"]["locator"] = amendments["source_locator"]
    approved = record["decision"] == "accepted"
    item["review"] = {
        "status": "approved" if approved else "rejected",
        "reviewer_id": record["reviewer_id"],
        "reviewed_at": record["reviewed_at"],
        "decision": record["decision"],
        "note": record.get("note"),
        "decision_authority": "human_teacher",
        "human_review_attestation": True,
        "checks": record["checks"],
    }
    item["dataset"]["tier"] = "gold" if approved else "rejected"
    item["dataset"]["version"] = version
    item["dataset"]["split"] = record["split"] if approved else "unassigned"
    return item


def _write_jsonl(path: Path, items: Iterable[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in items),
        encoding="utf-8",
    )


def freeze_gold(
    candidate_path: Path,
    audit_path: Path,
    version: str,
    gold_root: Path,
    rejected_root: Path,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    if not SEMVER.fullmatch(version):
        raise GovernanceError("Gold version must use MAJOR.MINOR.PATCH")
    candidates, audit = validate_audit(candidate_path, audit_path)
    accepted_records = [item for item in audit["records"] if item["decision"] == "accepted"]
    if not accepted_records:
        raise GovernanceError("no explicitly teacher-accepted items; Gold was not created")
    rejected_records = [item for item in audit["records"] if item["decision"] == "rejected"]
    gold_items = []
    for record in accepted_records:
        candidate = candidates[record["item_id"]]
        _verify_source(candidate, project_root)
        gold_items.append(_apply_review(candidate, record, version))
    rejected_items = [
        _apply_review(candidates[record["item_id"]], record, version)
        for record in rejected_records
    ]
    gold_root.mkdir(parents=True, exist_ok=True)
    rejected_root.mkdir(parents=True, exist_ok=True)
    stem = f"course_qa_v{version}"
    gold_path = gold_root / f"{stem}.jsonl"
    manifest_path = gold_root / f"{stem}_manifest.json"
    rejected_path = rejected_root / f"{stem}.jsonl"
    targets = [gold_path, manifest_path]
    if rejected_items:
        targets.append(rejected_path)
    if any(path.exists() for path in targets):
        raise GovernanceError("frozen version already exists; choose a new version")
    _write_jsonl(gold_path, sorted(gold_items, key=lambda item: item["item_id"]))
    if rejected_items:
        _write_jsonl(rejected_path, sorted(rejected_items, key=lambda item: item["item_id"]))
    split_counts = Counter(item["dataset"]["split"] for item in gold_items)
    subtype_counts = Counter(item.get("subtype", "unspecified") for item in gold_items)
    manifest = {
        "schema_version": "1.0.0",
        "dataset_name": "industrial_robot_course_qa_gold",
        "dataset_version": version,
        "dataset_tier": "gold",
        "frozen_at": _utc_now(),
        "immutable": True,
        "metric_eligibility": True,
        "item_count": len(gold_items),
        "counts": {
            "split": dict(sorted(split_counts.items())),
            "subtype": dict(sorted(subtype_counts.items())),
            "rejected": len(rejected_items),
            "pending": audit["counts"]["pending"],
        },
        "provenance": {
            "candidate_sha256": sha256_file(candidate_path),
            "review_audit_sha256": sha256_file(audit_path),
            "review_input_sha256": audit["review_input"]["sha256"],
            "review_batch_id": audit["review_batch_id"],
        },
        "artifact": {
            "file": gold_path.name,
            "sha256": sha256_file(gold_path),
            "hash_algorithm": "sha256",
        },
        "policy": {
            "accepted_decision_required": True,
            "human_teacher_attestation_required": True,
            "unreviewed_items_included": False,
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Teacher review and Gold dataset governance")
    subparsers = parser.add_subparsers(dest="command", required=True)
    template = subparsers.add_parser("review-template")
    template.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    template.add_argument("--output", type=Path, default=DEFAULT_REVIEW_CSV)
    importer = subparsers.add_parser("import-review")
    importer.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    importer.add_argument("--review-csv", type=Path, default=DEFAULT_REVIEW_CSV)
    importer.add_argument("--output", type=Path, default=DEFAULT_AUDIT)
    importer.add_argument("--review-batch-id", required=True)
    validator = subparsers.add_parser("validate-review")
    validator.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    validator.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    freezer = subparsers.add_parser("freeze")
    freezer.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    freezer.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    freezer.add_argument("--version", required=True)
    freezer.add_argument("--gold-root", type=Path, default=DEFAULT_GOLD_ROOT)
    freezer.add_argument("--rejected-root", type=Path, default=DEFAULT_REJECTED_ROOT)
    args = parser.parse_args()
    if args.command == "review-template":
        write_review_template(args.candidate, args.output)
        result = {"review_template": str(args.output)}
    elif args.command == "import-review":
        result = import_reviews(
            args.candidate, args.review_csv, args.output, args.review_batch_id
        )
    elif args.command == "validate-review":
        _, audit = validate_audit(args.candidate, args.audit)
        result = {"valid": True, "counts": audit["counts"]}
    else:
        result = freeze_gold(
            args.candidate,
            args.audit,
            args.version,
            args.gold_root,
            args.rejected_root,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
