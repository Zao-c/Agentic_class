import json
from pathlib import Path

from app.alarm_codes import AlarmCodeService
from app.config import Settings
from app.retrieval import Retriever
from app.schemas import AgentState, AlarmCodeRecord, RunStatus
from app.storage import Store
from app.tutoring import TutoringService
from app.workflow import AgentWorkflow


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
    assert records_by_number["3HAC020738-001"]["printed_pages"] == [79, 80, 81, 82, 83, 91]
    assert len(
        records_by_number["3HAC020738-001"]["applicability"]["verified_evidence"]
    ) == 28
    assert records_by_number["3HAC052355-001"]["official_pdf_sha256"] == (
        "AECA826E79406D8D86C402DE7156B29E551AF4F441A943519EC1E6D51E928031"
    )
    assert records_by_number["3HAC052355-001"]["revision"] == "Y"
    assert not list(REGISTRY_PATH.parent.glob("*.pdf"))


def test_official_alarm_records_validate_and_keep_evidence_boundaries():
    raw_records = _read(ALARMS_PATH)["records"]
    records = {item["code"]: AlarmCodeRecord.model_validate(item) for item in raw_records}

    assert set(records) == {
        "10002",
        "10009",
        "10010",
        "10011",
        "10012",
        "10013",
        "10014",
        "10015",
        "10016",
        "10017",
        "10018",
        "10019",
        "10020",
        "10021",
        "10024",
        "10025",
        "10026",
        "10027",
        "10030",
        "10034",
        "10035",
        "10036",
        "10037",
        "10038",
        "10039",
        "10041",
        "10060",
        "10420",
        "38213",
    }

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

    high_risk_pages = {
        "10013": 79,
        "10024": 80,
        "10027": 80,
        "10035": 81,
        "10037": 81,
        "10039": 82,
        "10420": 91,
    }
    for code, printed_page in high_risk_pages.items():
        record = records[code]
        assert record.equipment_models == ["*"]
        assert record.controller_versions == ["IRC5"]
        assert record.risk_level.value == "high"
        assert record.review_status == "source_verified"
        assert f"印刷页 {printed_page}" in (record.source_locator or "")
        assert record.forbidden_actions


def test_official_alarm_file_imports_with_model_and_risk_boundaries(tmp_path: Path):
    service = AlarmCodeService(Store(tmp_path / "alarm-registry.db"))
    assert service.import_file(ALARMS_PATH) == {"created": 29, "updated": 0, "total": 29}

    battery = service.lookup("38213", "ABB", "IRB120")
    assert battery["status"] == "exact_match"
    assert battery["matches"][0]["version"] == "3HAC035728-001-rev-W"

    wrong_model = service.lookup("38213", "ABB", "IRB2600")
    assert wrong_model["status"] == "model_mismatch"
    assert wrong_model["matches"] == []

    revolution_counter = service.lookup("10036", "ABB", "IRB120")
    assert revolution_counter["status"] == "brand_match_model_unverified"
    assert revolution_counter["matches"][0]["risk_level"] == "high"

    collision = service.lookup("10024", "ABB", "IRB120")
    assert collision["status"] == "brand_match_model_unverified"
    assert collision["matches"][0]["risk_level"] == "high"

    unsafe_path = service.lookup("10420", "ABB", "IRB120")
    assert unsafe_path["status"] == "brand_match_model_unverified"
    assert unsafe_path["matches"][0]["risk_level"] == "high"

    positive_state = service.lookup("10038", "ABB", "IRB120")
    assert positive_state["status"] == "brand_match_model_unverified"
    assert positive_state["matches"][0]["risk_level"] == "low"


def test_every_alarm_locator_maps_to_registered_document_revision_and_page():
    registry = _read(REGISTRY_PATH)
    sources = {record["document_number"]: record for record in registry["records"]}

    for record in _read(ALARMS_PATH)["records"]:
        document_number, revision = record["version"].split("-rev-", 1)
        source = sources[document_number]
        assert f"Revision {revision}" in record["source_locator"]
        assert any(
            f"印刷页 {page}" in record["source_locator"]
            for page in source["printed_pages"]
        )
        assert record["review_status"] == "source_verified"


def test_new_official_high_risk_events_fail_closed_in_portable_workflow(tmp_path: Path):
    settings = Settings(
        database_path=tmp_path / "official-alarm-workflow.db",
        knowledge_root=PROJECT_ROOT / "data" / "public_sample" / "abb_irb120_irc5_v1",
        reports_root=tmp_path / "reports",
        auto_ingest=False,
        auto_ingest_alarm_codes=False,
        auto_ingest_knowledge_points=False,
        agent_profile="portable",
    )
    settings.ensure_directories()
    store = Store(settings.database_path)
    retriever = Retriever(store, settings)
    retriever.import_directory(settings.knowledge_root, include_binary=False)
    alarms = AlarmCodeService(store)
    alarms.import_file(ALARMS_PATH)
    workflow = AgentWorkflow(store, retriever, alarms, TutoringService(store, retriever), settings)

    for code in ("10013", "10024", "10027", "10035", "10037", "10039", "10420"):
        state = AgentState(
            request_id=f"request-{code}",
            run_id=f"run-{code}",
            session_id=f"session-{code}",
            user_id="benchmark-evaluator",
            original_message=f"ABB IRB120 报警 {code}，故障发生在手动模式",
        )
        store.create_run(state)
        result = workflow.run(state)

        assert result.final_status == RunStatus.escalated
        assert result.risk_level.value == "high"
        assert result.stop_reason == "deterministic_safety_escalation"
        assert [item["tool_name"] for item in result.tool_history] == [
            "lookup_error_code",
            "manual_retrieval",
            "check_safety_constraint",
            "record_diagnostic_state",
        ]
        assert result.retrieved_evidence[0].title == "ABB Operating manual - Trouble shooting, IRC5"
