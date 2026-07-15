from tests.conftest import create_run


def test_document_import_and_duplicate_detection(client):
    payload = {
        "title": "教师确认资料",
        "content": "工具坐标系用于描述工具中心点相对于机器人末端法兰的位姿。",
        "document_type": "teacher_confirmed",
    }
    created = client.post(
        "/api/v1/knowledge/documents", headers={"X-Role": "teacher"}, json=payload
    )
    assert created.status_code == 201
    assert created.json()["status"] == "created"
    duplicate = client.post(
        "/api/v1/knowledge/documents", headers={"X-Role": "teacher"}, json=payload
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["status"] == "duplicate"


def test_unsupported_or_external_source_path_is_rejected(client, tmp_path):
    response = client.post(
        "/api/v1/knowledge/documents",
        headers={"X-Role": "teacher"},
        json={"title": "外部文件", "source_path": str(tmp_path / "outside.txt")},
    )
    assert response.status_code == 422


def test_evaluation_generates_real_report(client):
    response = client.post(
        "/api/v1/evaluations/run",
        headers={"X-Role": "maintainer"},
        json={"dataset_path": "cases.csv"},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["metrics"]["case_count"] == 1
    assert report["metrics"]["retrieval_nonempty_rate"] == 1.0
    assert len(report["report_files"]) == 2


def test_evaluation_comparison_runs_four_strategies(client):
    response = client.post(
        "/api/v1/evaluations/run",
        headers={"X-Role": "maintainer"},
        json={"dataset_path": "cases.csv", "compare": True},
    )
    assert response.status_code == 200, response.text
    comparison = response.json()
    assert set(comparison["strategies"]) == {"bm25", "embedding", "hybrid", "hybrid_rerank"}
    assert len(comparison["evaluation_run_ids"]) == 4


def test_no_evidence_abstains(client):
    run = create_run(client, "量子引力和火星殖民的课程要求是什么？")
    result = client.get(
        "/api/v1/runs/%s" % run["run_id"], headers={"X-User-ID": "student-1"}
    ).json()
    assert result["status"] == "abstained"
