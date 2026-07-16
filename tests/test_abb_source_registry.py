import json
from pathlib import Path

from app.alarm_codes import AlarmCodeService
from app.schemas import AlarmCodeRecord
from app.storage import Store


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "data" / "sources" / "abb_irb120_irc5_registry_v1.json"
ALARMS_PATH = PROJECT_ROOT / "data" / "structured" / "alarm_codes_v1.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_abb_registry_contains_metadata_only_official_sources():
    payload = _read(REGISTRY_PATH)
    assert payload["schema_version"] == "1.0.0"
    assert payload["status"] == "official_source_metadata_registry"
    assert payload["content_origin"] == "official_public_metadata_plus_original_summary"
    assert payload["teacher_reviewed"] is False
    assert "does not verify" in payload["claim_boundary"]
    assert len(payload["records"]) == 3

    expected_keys = {
        "title",
        "document_number",
        "revision",
        "official_url",
        "official_pdf_sha256",
        "printed_pages",
        "applicability",
        "copyright_boundary",
    }
    for record in payload["records"]:
        assert set(record) == expected_keys
        assert record["official_url"].startswith("https://library.e.abb.com/public/")
        assert "not redistributed" in record["copyright_boundary"]

    assert {item["document_number"] for item in payload["records"]} == {
        "3HAC020738-001",
        "3HAC035728-001",
        "3HAC052355-001",
    }
    records_by_number = {item["document_number"]: item for item in payload["records"]}
    assert records_by_number["3HAC035728-001"]["official_pdf_sha256"] == (
        "4CCD98DC0050018F32F290B976F7FD3A93400CBDDBFE0F6E9A06BBAB240520FA"
    )
    assert records_by_number["3HAC020738-001"]["official_pdf_sha256"] == (
        "5D99548225EF7ED1006839A614EDB931E1B46649E61A16BD71AA6105CEC37B35"
    )
    assert records_by_number["3HAC052355-001"]["official_pdf_sha256"] == (
        "AECA826E79406D8D86C402DE7156B29E551AF4F441A943519EC1E6D51E928031"
    )
    assert records_by_number["3HAC052355-001"]["revision"] == "Y"
    assert not list(REGISTRY_PATH.parent.glob("*.pdf"))


def test_official_alarm_records_validate_and_keep_evidence_boundaries():
    raw_records = _read(ALARMS_PATH)["records"]
    records = {item["code"]: AlarmCodeRecord.model_validate(item) for item in raw_records}

    battery = records["38213"]
    assert battery.equipment_models == ["IRB120"]
    assert battery.controller_versions == ["IRC5", "IRC5 Compact"]
    assert battery.version == "3HAC035728-001-rev-W"
    assert "印刷页 84" in (battery.source_locator or "")
    assert battery.review_status == "source_verified"

    revolution_counter = records["10036"]
    assert revolution_counter.equipment_models == ["*"]
    assert revolution_counter.controller_versions == ["IRC5"]
    assert revolution_counter.risk_level.value == "high"
    assert revolution_counter.version == "3HAC020738-001-rev-K"
    assert "印刷页 81" in (revolution_counter.source_locator or "")
    assert "RobotWare 版本待现场确认" in (revolution_counter.source_locator or "")
    assert any("不要在无教师" in action for action in revolution_counter.forbidden_actions)


def test_official_alarm_file_imports_with_model_and_risk_boundaries(tmp_path: Path):
    service = AlarmCodeService(Store(tmp_path / "alarm-registry.db"))
    assert service.import_file(ALARMS_PATH) == {"created": 2, "updated": 0, "total": 2}

    battery = service.lookup("38213", "ABB", "IRB120")
    assert battery["status"] == "exact_match"
    assert battery["matches"][0]["version"] == "3HAC035728-001-rev-W"

    wrong_model = service.lookup("38213", "ABB", "IRB2600")
    assert wrong_model["status"] == "model_mismatch"
    assert wrong_model["matches"] == []

    revolution_counter = service.lookup("10036", "ABB", "IRB120")
    assert revolution_counter["status"] == "brand_match_model_unverified"
    assert revolution_counter["matches"][0]["risk_level"] == "high"
