"""Governance and reproducibility tests for the public synthetic RAG set."""

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from scripts.generate_synthetic_retrieval_benchmark import generate


DATASET_RELATIVE_PATH = Path("data/eval/rag_synthetic_180_v1.csv")
MANIFEST_RELATIVE_PATH = Path("data/eval/rag_synthetic_180_v1_manifest.json")
CORPUS_RELATIVE_ROOT = Path("data/public_sample/synthetic_classroom_v1")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(root: Path) -> list[dict[str, str]]:
    with (root / DATASET_RELATIVE_PATH).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_generation_is_byte_deterministic(tmp_path: Path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"

    first_manifest = generate(first_root)
    second_manifest = generate(second_root)

    assert first_manifest == second_manifest
    assert (first_root / DATASET_RELATIVE_PATH).read_bytes() == (
        second_root / DATASET_RELATIVE_PATH
    ).read_bytes()
    assert (first_root / MANIFEST_RELATIVE_PATH).read_bytes() == (
        second_root / MANIFEST_RELATIVE_PATH
    ).read_bytes()

    first_sources = sorted((first_root / CORPUS_RELATIVE_ROOT).glob("*.md"))
    second_sources = sorted((second_root / CORPUS_RELATIVE_ROOT).glob("*.md"))
    assert [item.name for item in first_sources] == [item.name for item in second_sources]
    assert [item.read_bytes() for item in first_sources] == [
        item.read_bytes() for item in second_sources
    ]


def test_dataset_has_180_cases_60_families_and_grouped_splits(tmp_path: Path):
    generate(tmp_path)
    rows = _rows(tmp_path)

    assert len(rows) == 180
    assert len({row["id"] for row in rows}) == 180
    assert Counter(row["split"] for row in rows) == {
        "train": 108,
        "dev": 36,
        "test": 36,
    }

    by_family: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_family[row["family_id"]].append(row)
    assert len(by_family) == 60
    assert all(len(items) == 3 for items in by_family.values())
    assert all(
        {item["variant_id"] for item in items} == {"1", "2", "3"}
        for items in by_family.values()
    )
    assert all(
        len({item["split"] for item in items}) == 1
        for items in by_family.values()
    )


def test_manifest_counts_and_hashes_match_written_artifacts(tmp_path: Path):
    returned = generate(tmp_path)
    manifest = json.loads(
        (tmp_path / MANIFEST_RELATIVE_PATH).read_text(encoding="utf-8")
    )

    assert manifest == returned
    assert manifest["case_count"] == 180
    assert manifest["family_count"] == 60
    assert manifest["variants_per_family"] == 3
    assert manifest["split_case_counts"] == {
        "dev": 36,
        "test": 36,
        "train": 108,
    }
    assert sum(manifest["category_case_counts"].values()) == 180
    assert manifest["generation"]["uses_llm"] is False
    assert manifest["generation"]["blueprint_sha256"]
    assert manifest["artifacts"]["dataset_sha256"] == _sha256(
        tmp_path / DATASET_RELATIVE_PATH
    )

    source_hashes = manifest["artifacts"]["source_sha256"]
    assert len(source_hashes) == manifest["source_document_count"] == 10
    assert source_hashes == {
        path.name: _sha256(path)
        for path in sorted((tmp_path / CORPUS_RELATIVE_ROOT).glob("*.md"))
    }


def test_every_case_is_synthetic_student_data_and_never_gold(tmp_path: Path):
    generate(tmp_path)
    rows = _rows(tmp_path)
    manifest = json.loads(
        (tmp_path / MANIFEST_RELATIVE_PATH).read_text(encoding="utf-8")
    )

    assert {row["actor_role"] for row in rows} == {"synthetic_student"}
    assert {row["label_authority"] for row in rows} == {
        "deterministic_synthetic_spec"
    }
    assert {row["teacher_reviewed"] for row in rows} == {"false"}
    assert {row["metric_eligibility"] for row in rows} == {
        "synthetic_engineering_only"
    }
    assert manifest["status"] == "synthetic_engineering_validation"
    assert manifest["simulation"] is True
    assert manifest["content_origin"] == "synthetic_public_original"
    assert manifest["teacher_reviewed"] is False
    assert manifest["human_review_attestation"] is False
    assert manifest["formal_comparison_eligible"] is False
    assert manifest["metric_eligibility"] == "synthetic_engineering_only"
    assert "not Gold" in manifest["claim_boundary"]

    for source in (tmp_path / CORPUS_RELATIVE_ROOT).glob("*.md"):
        text = source.read_text(encoding="utf-8")
        assert "完全原创" in text
        assert "不适用于任何真实机器人" in text


def test_missing_and_prompt_injection_cases_have_no_expected_evidence(tmp_path: Path):
    generate(tmp_path)
    rows = _rows(tmp_path)

    missing = [row for row in rows if row["category"] == "资料缺失"]
    injections = [row for row in rows if row["category"] == "Prompt 注入"]
    assert missing
    assert injections
    for row in missing + injections:
        assert row["expected_sources"] == ""
        assert row["expected_points"] == ""
        assert row["expect_evidence"] == "false"
