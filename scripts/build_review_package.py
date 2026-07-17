"""Build an isolated simulation-only review package.

The package helps a teacher triage candidate QA and source-verified alarm
records.  It never writes teacher identity, human attestation or Gold-eligible
decisions, and it is deliberately rejected by ``manage_gold_dataset.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.manage_gold_dataset import load_candidates, sha256_file


DEFAULT_CANDIDATE = PROJECT_ROOT / "data/datasets/candidate/course_qa_v1.jsonl"
DEFAULT_ALARMS = PROJECT_ROOT / "data/structured/alarm_codes_v1.json"
DEFAULT_REGISTRY = PROJECT_ROOT / "data/sources/abb_irb120_irc5_registry_v1.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "runtime/review-packages/simulated-review-v1/package.json"


class ReviewPackageError(ValueError):
    """Raised when a simulation package could cross the human-review boundary."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ReviewPackageError("review package inputs must stay inside project root") from exc


def _registry_index(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {record["document_number"]: record for record in registry["records"]}


def _document_number(version: str) -> str:
    marker = "-rev-"
    if marker not in version:
        raise ReviewPackageError(f"alarm version has no revision marker: {version}")
    return version.split(marker, 1)[0]


def _qa_record(item: dict[str, Any]) -> dict[str, Any]:
    source = item.get("source", {})
    return {
        "item_id": item["item_id"],
        "item_type": "course_qa",
        "source_item_sha256": _canonical_sha256(item),
        "review_status": "simulated_review",
        "review_authority": "simulation",
        "suggested_decision": "needs_human_review",
        "suggested_amendments": [],
        "suggested_checks": {
            "source_hash_present": bool(source.get("sha256")),
            "source_locator_present": bool(source.get("locator")),
            "privacy_requires_human_check": True,
            "safety_requires_human_check": True,
        },
        "review_note": "机器预检仅用于排序；题面、答案、来源、隐私和安全仍须教师逐条确认。",
        "candidate_payload": item,
    }


def _alarm_record(
    item: dict[str, Any], registry_by_number: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    document_number = _document_number(str(item.get("version", "")))
    source = registry_by_number.get(document_number)
    if source is None:
        raise ReviewPackageError(
            f"alarm {item.get('code')}: source document {document_number} is not registered"
        )
    locator = str(item.get("source_locator", ""))
    printed_page_matches = any(
        f"印刷页 {page}" in locator for page in source.get("printed_pages", [])
    )
    if not printed_page_matches:
        raise ReviewPackageError(
            f"alarm {item.get('code')}: locator does not map to a registered printed page"
        )
    return {
        "item_id": f"alarm:{item['equipment_brand'].lower()}:{item['code']}:{item['version']}",
        "item_type": "alarm_code",
        "source_item_sha256": _canonical_sha256(item),
        "review_status": "simulated_review",
        "review_authority": "simulation",
        "suggested_decision": "source_metadata_ready_for_teacher_scope_review",
        "suggested_amendments": [],
        "suggested_checks": {
            "official_document_registered": True,
            "official_pdf_sha256_present": bool(source.get("official_pdf_sha256")),
            "document_number_and_revision_match": True,
            "printed_page_registered": True,
            "equipment_scope_explicit": bool(item.get("equipment_models")),
            "forbidden_actions_present": bool(item.get("forbidden_actions")),
            "school_device_scope_requires_human_check": True,
        },
        "review_note": (
            "官方来源元数据已机器核对；风险分级、学校实机型号、控制器变体和 "
            "RobotWare 适用性仍须教师确认。"
        ),
        "candidate_payload": item,
        "source_evidence": {
            "document_number": source["document_number"],
            "revision": source["revision"],
            "official_url": source["official_url"],
            "official_pdf_sha256": source["official_pdf_sha256"],
            "source_locator": item["source_locator"],
        },
    }


def validate_package(package: dict[str, Any]) -> None:
    required_false = (
        "teacher_reviewed",
        "human_review_attestation",
        "metric_eligibility",
        "gold_freeze_eligible",
    )
    if package.get("artifact_type") != "simulated_review_package":
        raise ReviewPackageError("artifact_type must be simulated_review_package")
    if package.get("review_mode") != "simulation":
        raise ReviewPackageError("review_mode must be simulation")
    if any(package.get(field) is not False for field in required_false):
        raise ReviewPackageError("simulation package cannot claim teacher, metric or Gold eligibility")
    records = package.get("records")
    if not isinstance(records, list) or not records:
        raise ReviewPackageError("simulation package records must be a non-empty list")
    seen: set[str] = set()
    for record in records:
        item_id = record.get("item_id")
        if not item_id or item_id in seen:
            raise ReviewPackageError(f"invalid or duplicate item_id: {item_id}")
        seen.add(item_id)
        if record.get("review_status") != "simulated_review":
            raise ReviewPackageError(f"{item_id}: review_status must be simulated_review")
        if record.get("review_authority") != "simulation":
            raise ReviewPackageError(f"{item_id}: review_authority must be simulation")
        if any(key in record for key in ("reviewer_id", "reviewer_role", "reviewed_at")):
            raise ReviewPackageError(f"{item_id}: simulated record cannot carry teacher identity")
        if not isinstance(record.get("source_item_sha256"), str) or len(
            record["source_item_sha256"]
        ) != 64:
            raise ReviewPackageError(f"{item_id}: source_item_sha256 is invalid")


def build_package(
    *,
    candidate_path: Path,
    alarms_path: Path,
    registry_path: Path,
    package_id: str,
    generated_at: str | None = None,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    candidates = load_candidates(candidate_path)
    alarms_payload = json.loads(alarms_path.read_text(encoding="utf-8"))
    registry_payload = json.loads(registry_path.read_text(encoding="utf-8"))
    registry_by_number = _registry_index(registry_payload)
    records = [_qa_record(candidates[item_id]) for item_id in sorted(candidates)]
    records.extend(
        _alarm_record(item, registry_by_number)
        for item in sorted(alarms_payload["records"], key=lambda value: value["code"])
    )
    counts = Counter(record["item_type"] for record in records)
    package = {
        "schema_version": "1.0.0",
        "artifact_type": "simulated_review_package",
        "package_id": package_id,
        "generated_at": generated_at or _utc_now(),
        "review_mode": "simulation",
        "teacher_reviewed": False,
        "human_review_attestation": False,
        "metric_eligibility": False,
        "gold_freeze_eligible": False,
        "source_snapshots": [
            {
                "kind": "course_qa_candidate",
                "relative_path": _relative(candidate_path, project_root),
                "sha256": sha256_file(candidate_path),
                "item_count": len(candidates),
            },
            {
                "kind": "alarm_code",
                "relative_path": _relative(alarms_path, project_root),
                "sha256": sha256_file(alarms_path),
                "item_count": len(alarms_payload["records"]),
            },
            {
                "kind": "official_source_registry",
                "relative_path": _relative(registry_path, project_root),
                "sha256": sha256_file(registry_path),
                "item_count": len(registry_payload["records"]),
            },
        ],
        "status_counts": {"simulated_review": len(records)},
        "item_type_counts": dict(sorted(counts.items())),
        "records": records,
        "claim_boundary": (
            "Simulation-only triage artifact. It is not human teacher review, not Gold, "
            "not metric-eligible and cannot be consumed by the Gold freeze workflow."
        ),
    }
    validate_package(package)
    return package


def public_summary(package: dict[str, Any]) -> dict[str, Any]:
    summary = {
        key: package[key]
        for key in (
            "schema_version",
            "package_id",
            "generated_at",
            "review_mode",
            "teacher_reviewed",
            "human_review_attestation",
            "metric_eligibility",
            "gold_freeze_eligible",
            "source_snapshots",
            "status_counts",
            "item_type_counts",
            "claim_boundary",
        )
    }
    summary["artifact_type"] = "simulated_review_package_summary"
    summary["source_artifact_type"] = package["artifact_type"]
    summary["content_included"] = False
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an isolated simulated review package")
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--alarms", type=Path, default=DEFAULT_ALARMS)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--public-summary", type=Path)
    parser.add_argument("--package-id", default="simulated-review-v1")
    parser.add_argument("--generated-at")
    args = parser.parse_args()
    package = build_package(
        candidate_path=args.candidate,
        alarms_path=args.alarms,
        registry_path=args.registry,
        package_id=args.package_id,
        generated_at=args.generated_at,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.public_summary:
        args.public_summary.parent.mkdir(parents=True, exist_ok=True)
        args.public_summary.write_text(
            json.dumps(public_summary(package), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(public_summary(package), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
