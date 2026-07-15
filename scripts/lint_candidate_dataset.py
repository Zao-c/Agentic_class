from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT_ROOT / "data" / "datasets" / "candidate" / "course_qa_v1.jsonl"
ALLOWED_SPLITS = {"unassigned", "train", "dev", "test"}
ASSIGNED_SPLITS = {"train", "dev", "test"}
REQUIRED_TOP_LEVEL = {
    "item_id",
    "task_type",
    "subtype",
    "input_turns",
    "reference_answer",
    "expected_status",
    "expected_tools",
    "risk_level",
    "source",
    "review",
    "dataset",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def dedup_key(question: str) -> str:
    normalized = re.sub(r"\W+", "", question).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def _question(item: dict[str, Any]) -> str:
    turns = item.get("input_turns")
    if not isinstance(turns, list):
        return ""
    for turn in reversed(turns):
        if isinstance(turn, dict) and turn.get("role") in {"user", "student"}:
            return str(turn.get("content") or "").strip()
    return ""


def _issue(code: str, **details: Any) -> dict[str, Any]:
    return {"code": code, **details}


def lint_items(
    items: list[dict[str, Any]],
    project_root: Path | None = None,
    verify_sources: bool = False,
) -> dict[str, Any]:
    """Validate candidate QA without making review or Gold decisions.

    The report intentionally identifies records by item ID only. It never copies
    question, answer, excerpt, locator, or source-path text into findings.
    """

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    item_ids: Counter[str] = Counter()
    dedup_groups: dict[str, list[str]] = defaultdict(list)
    leakage_groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    split_by_item_id: dict[str, str] = {}
    root = (project_root or PROJECT_ROOT).resolve()

    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(_issue("item_not_object", row=index))
            continue
        item_id = str(item.get("item_id") or "").strip()
        safe_id = item_id or f"row:{index}"
        if not item_id:
            errors.append(_issue("missing_item_id", row=index))
        else:
            item_ids[item_id] += 1

        missing = sorted(REQUIRED_TOP_LEVEL - set(item))
        if missing:
            errors.append(_issue("missing_required_fields", item_id=safe_id, fields=missing))

        question = _question(item)
        if not question:
            errors.append(_issue("missing_user_question", item_id=safe_id))
        answer = item.get("reference_answer")
        if not isinstance(answer, str) or not answer.strip():
            errors.append(_issue("missing_reference_answer", item_id=safe_id))

        dataset = item.get("dataset") if isinstance(item.get("dataset"), dict) else {}
        if dataset.get("tier") != "candidate":
            errors.append(_issue("invalid_dataset_tier", item_id=safe_id))
        split = str(dataset.get("split") or "")
        split_by_item_id[safe_id] = split
        if split not in ALLOWED_SPLITS:
            errors.append(_issue("invalid_split", item_id=safe_id, split=split))
        group = str(dataset.get("dedup_group") or "").strip()
        if not group:
            errors.append(_issue("missing_dedup_group", item_id=safe_id))
        else:
            dedup_groups[group].append(safe_id)
            if question and re.fullmatch(r"[0-9a-f]{20}", group) and group != dedup_key(question):
                errors.append(_issue("dedup_group_mismatch", item_id=safe_id))
        leakage_group = str(dataset.get("leakage_group") or "").strip()
        if not leakage_group:
            errors.append(_issue("missing_leakage_group", item_id=safe_id))
        else:
            leakage_groups[leakage_group].append((safe_id, split))

        review = item.get("review") if isinstance(item.get("review"), dict) else {}
        if review.get("status") != "needs_teacher_review":
            errors.append(_issue("invalid_candidate_review_status", item_id=safe_id))
        if review.get("decision") not in (None, ""):
            errors.append(_issue("candidate_contains_review_decision", item_id=safe_id))

        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        for field in ("relative_path", "sha256", "locator"):
            if not str(source.get(field) or "").strip():
                errors.append(
                    _issue("missing_required_field", item_id=safe_id, field=f"source.{field}")
                )
        expected_hash = str(source.get("sha256") or "")
        if expected_hash and not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            errors.append(_issue("invalid_source_sha256", item_id=safe_id))
        if verify_sources and source.get("relative_path"):
            source_path = (root / str(source["relative_path"])).resolve()
            try:
                source_path.relative_to(root)
            except ValueError:
                errors.append(_issue("source_path_outside_project", item_id=safe_id))
            else:
                if not source_path.is_file():
                    errors.append(_issue("source_file_missing", item_id=safe_id))
                elif expected_hash and sha256_file(source_path) != expected_hash:
                    errors.append(_issue("source_hash_mismatch", item_id=safe_id))

    for item_id, count in sorted(item_ids.items()):
        if count > 1:
            errors.append(_issue("duplicate_item_id", item_id=item_id, count=count))

    duplicate_groups = {group: ids for group, ids in dedup_groups.items() if len(ids) > 1}
    for group, ids in sorted(duplicate_groups.items()):
        warnings.append(
            _issue("duplicate_group", group=group, item_ids=sorted(ids), count=len(ids))
        )

    for kind, groups in (("dedup", dedup_groups), ("leakage", leakage_groups)):
        for group, members in groups.items():
            if kind == "dedup":
                assigned = {split_by_item_id.get(item_id, "") for item_id in members}
                member_ids = members
            else:
                assigned = {split for _, split in members}
                member_ids = [item_id for item_id, _ in members]
            assigned &= ASSIGNED_SPLITS
            if len(assigned) > 1:
                errors.append(
                    _issue(
                        f"{kind}_group_split_leakage",
                        group=group,
                        item_ids=sorted(member_ids),
                        splits=sorted(assigned),
                    )
                )

    duplicate_item_count = sum(len(ids) for ids in duplicate_groups.values())
    return {
        "schema_version": "1.0.0",
        "total": len(items),
        "errors": errors,
        "warnings": warnings,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "duplicate_group_count": len(duplicate_groups),
        "duplicate_item_count": duplicate_item_count,
        "duplicate_rate": round(duplicate_item_count / len(items), 4) if items else 0.0,
    }


def build_public_summary(
    items: list[dict[str, Any]],
    dataset_sha256: str,
    audited_on: str,
) -> dict[str, Any]:
    subtype_counts = Counter(str(item.get("subtype") or "unknown") for item in items)
    review_counts = Counter(
        str((item.get("review") or {}).get("status") or "unknown")
        for item in items
        if isinstance(item, dict)
    )
    source_count = len(
        {
            str((item.get("source") or {}).get("relative_path") or "")
            for item in items
            if isinstance(item, dict) and (item.get("source") or {}).get("relative_path")
        }
    )
    groups = Counter(
        str((item.get("dataset") or {}).get("dedup_group") or "")
        for item in items
        if isinstance(item, dict) and (item.get("dataset") or {}).get("dedup_group")
    )
    duplicate_groups = {group: count for group, count in groups.items() if count > 1}
    duplicate_item_count = sum(duplicate_groups.values())
    return {
        "schema_version": "1.0.0",
        "dataset_id": "course_qa_v1",
        "audited_on": audited_on,
        "dataset_sha256": dataset_sha256,
        "dataset_tier": "candidate",
        "content_published": False,
        "teacher_reviewed": False,
        "metric_eligibility": False,
        "total": len(items),
        "counts": dict(sorted(subtype_counts.items())),
        "source_count": source_count,
        "source_file_count": source_count,
        "review_status_counts": dict(sorted(review_counts.items())),
        "duplicate_group_count": len(duplicate_groups),
        "duplicate_item_count": duplicate_item_count,
        "duplicate_rate": round(duplicate_item_count / len(items), 4) if items else 0.0,
        "claim_boundary": (
            "Aggregate evidence for a private candidate snapshot. It is not Gold data and must not "
            "be used to claim production quality or formal benchmark accuracy."
        ),
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"row {number} is not a JSON object")
        items.append(value)
    return items


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint private candidate QA without making review decisions"
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--public-summary", type=Path)
    parser.add_argument("--audited-on", default=date.today().isoformat())
    parser.add_argument("--verify-sources", action="store_true")
    parser.add_argument("--fail-on-warning", action="store_true")
    args = parser.parse_args()

    dataset = args.dataset.resolve()
    items = load_jsonl(dataset)
    report = lint_items(items, project_root=PROJECT_ROOT, verify_sources=args.verify_sources)
    report["dataset_sha256"] = sha256_file(dataset)
    if args.report:
        write_json(args.report, report)
    if args.public_summary and not report["error_count"]:
        write_json(
            args.public_summary,
            build_public_summary(items, report["dataset_sha256"], args.audited_on),
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    blocked = report["error_count"] or (args.fail_on_warning and report["warning_count"])
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
