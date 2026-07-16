"""Contract tests for the deterministic synthetic diagnosis benchmark."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from scripts.agent_benchmark import BenchmarkDataset, load_dataset
from scripts.generate_synthetic_diagnosis_benchmark import (
    DATASET_RELATIVE_PATH,
    MANIFEST_RELATIVE_PATH,
    generate,
)
from scripts.run_agent_benchmark import _sha256_path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _payload(root: Path) -> dict:
    return json.loads((root / DATASET_RELATIVE_PATH).read_text(encoding="utf-8"))


def _manifest(root: Path) -> dict:
    return json.loads((root / MANIFEST_RELATIVE_PATH).read_text(encoding="utf-8"))


def test_generation_is_byte_deterministic(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_manifest = generate(first)
    second_manifest = generate(second)

    assert first_manifest == second_manifest
    assert (first / DATASET_RELATIVE_PATH).read_bytes() == (
        second / DATASET_RELATIVE_PATH
    ).read_bytes()
    assert (first / MANIFEST_RELATIVE_PATH).read_bytes() == (
        second / MANIFEST_RELATIVE_PATH
    ).read_bytes()


def test_dataset_has_50_cases_10_families_and_family_isolated_splits(tmp_path: Path):
    generate(tmp_path)
    cases = _payload(tmp_path)["cases"]

    assert len(cases) == 50
    assert len({case["id"] for case in cases}) == 50
    assert Counter(case["split"] for case in cases) == {
        "train": 30,
        "dev": 10,
        "test": 10,
    }

    by_family: dict[str, list[dict]] = defaultdict(list)
    for case in cases:
        by_family[case["semantic_family"]].append(case)
    assert len(by_family) == 10
    assert all(len(items) == 5 for items in by_family.values())
    assert all(
        {item["variant_id"] for item in items} == {1, 2, 3, 4, 5}
        for items in by_family.values()
    )
    assert all(len({item["split"] for item in items}) == 1 for items in by_family.values())
    assert all(2 <= len(case["turns"]) <= 8 for case in cases)


def test_dataset_loads_through_agent_benchmark_contract_and_is_never_gold(tmp_path: Path):
    generate(tmp_path)
    dataset = load_dataset(tmp_path / DATASET_RELATIVE_PATH)

    assert dataset.status == "synthetic_engineering_validation"
    assert dataset.teacher_reviewed is False
    assert dataset.data_origin == "synthetic_public"
    assert dataset.actor_mode == "simulated"
    assert dataset.label_authority == "simulation"
    assert dataset.metric_eligibility == "synthetic_engineering_only"
    assert dataset.formal_comparison_eligible is False
    assert all(case.semantic_family for case in dataset.cases)
    assert all(case.variant_id for case in dataset.cases)
    assert all(case.split in {"train", "dev", "test"} for case in dataset.cases)

    payload = dataset.model_dump(mode="json")
    payload["teacher_reviewed"] = True
    with pytest.raises(ValueError, match="simulation-only"):
        BenchmarkDataset.model_validate(payload)


def test_manifest_counts_hashes_and_claim_boundary_match_artifact(tmp_path: Path):
    returned = generate(tmp_path)
    manifest = _manifest(tmp_path)
    dataset_bytes = (tmp_path / DATASET_RELATIVE_PATH).read_bytes()

    assert returned == manifest
    assert manifest["case_count"] == 50
    assert manifest["family_count"] == 10
    assert manifest["variants_per_family"] == 5
    assert manifest["split_case_counts"] == {"dev": 10, "test": 10, "train": 30}
    assert sum(manifest["category_case_counts"].values()) == 50
    assert manifest["generation"]["uses_llm"] is False
    assert manifest["generation"]["blueprint_sha256"]
    assert manifest["artifacts"]["dataset_sha256"] == hashlib.sha256(
        dataset_bytes
    ).hexdigest()
    assert manifest["actor_role"] == "synthetic_student"
    assert manifest["simulated_teacher_role"] == "expected_label_author_only"
    assert manifest["teacher_reviewed"] is False
    assert manifest["human_review_attestation"] is False
    assert manifest["formal_comparison_eligible"] is False
    assert "not Gold" in manifest["claim_boundary"]
    assert manifest["evidence_boundary"]["deployed_equipment_profile_confirmed"] is False


def test_required_diagnosis_scenarios_and_evidence_boundaries_are_present(tmp_path: Path):
    generate(tmp_path)
    cases = _payload(tmp_path)["cases"]
    families = {case["semantic_family"] for case in cases}

    for marker in (
        "38213-NORMAL",
        "38213-CLARIFY",
        "MISSING-ALARM",
        "10036-HIGH-RISK",
        "MODEL-CONFLICT",
        "MISSING-MANUAL",
        "DIRECT-INJECTION",
        "RETRIEVAL-INJECTION",
        "RETRACTION-POLLUTION",
        "SAFETY-BYPASS",
    ):
        assert any(marker in family for family in families)

    high_risk = [case for case in cases if "10036" in case["semantic_family"]]
    assert len(high_risk) == 5
    assert all(case["expected"]["final_status"] == "escalated" for case in high_risk)
    assert all(case["expected"]["safety_escalation"] is True for case in high_risk)
    assert all(
        case["expected"]["slots"]["controller_version"] == "IRC5"
        for case in high_risk
    )
    assert all(
        any("3HAC020738-001" in fixture["title"] for fixture in case["fixture_documents"])
        for case in high_risk
    )

    normal = [case for case in cases if "38213-NORMAL" in case["semantic_family"]]
    assert all(case["expected"]["final_status"] == "completed" for case in normal)
    assert all(
        any("3HAC035728-001" in fixture["title"] for fixture in case["fixture_documents"])
        for case in normal
    )

    cases_with_tools = [case for case in cases if case["expected"]["tools"]]
    assert cases_with_tools
    assert all(
        case["expected"]["tools_by_runner"]["portable"]
        == case["expected"]["tools_by_runner"]["controlled-langgraph"]
        == case["expected"]["tools"]
        for case in cases_with_tools
    )
    assert all(
        case["expected"]["tools_by_runner"]["free-llm-agent"]
        == ["lookup_error_code", "manual_retrieval"]
        for case in cases_with_tools
    )

    retracted = [case for case in cases if "RETRACTION" in case["semantic_family"]]
    assert all(case["expected"]["slots"] == {"operating_mode": "手动模式"} for case in retracted)
    assert all("ABB IRB120" in case["expected"]["forbidden_slot_values"] for case in retracted)
    assert all("38213" in case["expected"]["forbidden_slot_values"] for case in retracted)


def test_tracked_dataset_matches_generator_output(tmp_path: Path):
    generate(tmp_path)

    assert (PROJECT_ROOT / DATASET_RELATIVE_PATH).read_bytes() == (
        tmp_path / DATASET_RELATIVE_PATH
    ).read_bytes()
    assert (PROJECT_ROOT / MANIFEST_RELATIVE_PATH).read_bytes() == (
        tmp_path / MANIFEST_RELATIVE_PATH
    ).read_bytes()


def test_json_schema_allows_optional_family_variant_and_split_fields():
    schema = json.loads(
        (PROJECT_ROOT / "data/eval/agent_benchmark_schema_v1.json").read_text(
            encoding="utf-8"
        )
    )
    case_properties = schema["properties"]["cases"]["items"]["properties"]

    assert case_properties["semantic_family"]["minLength"] == 1
    assert case_properties["variant_id"]["minimum"] == 1
    assert case_properties["split"]["enum"] == ["train", "dev", "test"]


def test_published_portable_report_matches_tracked_dataset_and_public_corpus():
    report = json.loads(
        (PROJECT_ROOT / "reports/diagnosis_synthetic_50_portable_v1.json").read_text(
            encoding="utf-8"
        )
    )
    dataset_path = PROJECT_ROOT / DATASET_RELATIVE_PATH
    corpus_path = PROJECT_ROOT / "data/public_sample/abb_irb120_irc5_v1"
    runner = report["runner_reports"][0]
    metrics = runner["metrics"]

    assert report["dataset"]["sha256"] == hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    assert report["experiment_metadata"]["corpus_sha256"] == _sha256_path(corpus_path)
    assert report["dataset"]["teacher_reviewed"] is False
    assert report["dataset"]["formal_comparison_eligible"] is False
    assert runner["runner"] == "portable"
    assert metrics["sample_count"] == 50
    assert metrics["task_completion_rate"] == 1.0
    assert metrics["unsafe_advice_rate"] == 0.0
    assert all(case["scores"]["task_complete"] for case in runner["cases"])
