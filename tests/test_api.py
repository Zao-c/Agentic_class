from tests.conftest import create_run


def test_health_and_ready(client):
    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/ready").json()
    assert ready["status"] == "ready"
    assert ready["indexed_chunks"] >= 1
    assert ready["alarm_codes"]["active_records"] == 2
    assert ready["retrieval"] == {
        "strategy": "hybrid_rerank",
        "neural_required": False,
        "hf_cache_exists": None,
        "local_files_only": None,
    }


def test_knowledge_question_has_trace_and_citations(client):
    run = create_run(client, "示教编程的一般步骤是什么？")
    response = client.get(
        "/api/v1/runs/%s" % run["run_id"], headers={"X-User-ID": "student-1"}
    )
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "completed"
    assert result["citations"]
    assert "课程资料" in result["answer"]
    assert "1. 根据任务要求" in result["answer"]
    assert "低速、单步" in result["answer"]
    assert result["citations"][0]["chunk_id"]
    assert result["citations"][0]["retrieval_method"] == "hybrid_rerank"
    assert result["citations"][0]["score_components"]

    trace = client.get(
        "/api/v1/traces/%s" % run["request_id"], headers={"X-User-ID": "student-1"}
    ).json()
    event_types = {event["event_type"] for event in trace["events"]}
    assert {"intent.classified", "tool.finished", "evidence.judged", "answer.completed"} <= event_types
    assert trace["anonymous_user_id"] != "student-1"
    assert trace["state"]["configuration"]["retrieval_strategy"] == "hybrid_rerank"
    evidence_event = next(event for event in trace["events"] if event["event_type"] == "evidence.judged")
    assert "coverage" in evidence_event["data"]
    assert "reasons" in evidence_event["data"]


def test_sse_replays_events_and_closes(client):
    run = create_run(client, "示教编程的一般步骤是什么？")
    response = client.get(run["stream_url"])
    assert response.status_code == 200
    assert "event: intent.classified" in response.text
    assert "event: stream.closed" in response.text


def test_session_owner_isolation(client):
    run = create_run(client, "示教编程的一般步骤是什么？", user_id="owner")
    denied = client.get(
        "/api/v1/runs/%s" % run["run_id"], headers={"X-User-ID": "another-user"}
    )
    assert denied.status_code == 403


def test_negative_feedback_creates_bad_case(client):
    run = create_run(client, "示教编程的一般步骤是什么？")
    response = client.post(
        "/api/v1/runs/%s/feedback" % run["run_id"],
        headers={"X-User-ID": "student-1"},
        json={"rating": 1, "helpful": False, "comment": "步骤不完整", "tags": ["citation"]},
    )
    assert response.status_code == 200
    bad_case_id = response.json()["bad_case_id"]
    assert bad_case_id.startswith("bad_")
    bad_cases = client.get("/api/v1/bad-cases", headers={"X-Role": "teacher"}).json()["items"]
    assert len(bad_cases) == 1

    detail = client.get(
        "/api/v1/bad-cases/%s" % bad_case_id,
        headers={"X-Role": "teacher"},
    )
    assert detail.status_code == 200
    assert detail.json()["trace"]["anonymous_user_id"] != "student-1"

    review = client.put(
        "/api/v1/bad-cases/%s/review" % bad_case_id,
        headers={"X-Role": "teacher"},
        json={
            "expected_status": "completed",
            "expected_task_type": "knowledge_qa",
            "expected_risk_level": "low",
            "answer_must_contain": ["课程资料"],
            "answer_must_not_contain": ["量子引力"],
            "require_citations": True,
            "review_note": "固定知识问答基础行为",
        },
    )
    assert review.status_code == 200

    exported = client.get(
        "/api/v1/bad-cases/%s/export" % bad_case_id,
        headers={"X-Role": "maintainer"},
    ).json()
    exported_again = client.get(
        "/api/v1/bad-cases/%s/export" % bad_case_id,
        headers={"X-Role": "maintainer"},
    ).json()
    assert exported["schema_version"] == "1.0.0"
    assert exported["package_hash"] == exported_again["package_hash"]
    assert exported["assertions"]["expected_status"] == "completed"

    replay = client.post(
        "/api/v1/bad-cases/%s/replay" % bad_case_id,
        headers={"X-Role": "maintainer"},
    ).json()
    assert replay["passed"] is True
    assert replay["result"]["citation_count"] >= 1

    promoted = client.post(
        "/api/v1/bad-cases/%s/promote" % bad_case_id,
        headers={"X-Role": "maintainer"},
    )
    assert promoted.status_code == 201
    regression = client.post(
        "/api/v1/regressions/run",
        headers={"X-Role": "maintainer"},
        json={},
    ).json()
    assert regression["case_count"] == 1
    assert regression["pass_rate"] == 1.0

    metrics = client.get(
        "/api/v1/operations/metrics?hours=24",
        headers={"X-Role": "teacher"},
    ).json()
    assert metrics["runs"]["total"] >= 1
    assert metrics["tools"]["calls"] >= 1
    assert metrics["feedback"]["count"] == 1
    assert metrics["bad_cases"]["reviewed"] == 1


def test_trace_recursively_redacts_personal_data(client):
    phone = "13812345678"  # public-audit: allow=china_mobile_number
    email = "student@example.com"  # public-audit: allow=email_address
    run = create_run(client, "示教编程步骤是什么？联系电话%s，邮箱%s" % (phone, email))
    trace = client.get(
        "/api/v1/traces/%s" % run["request_id"],
        headers={"X-User-ID": "student-1"},
    ).text
    assert phone not in trace
    assert email not in trace
    assert "[PHONE_REDACTED]" in trace
    assert "[EMAIL_REDACTED]" in trace


def test_learning_records_can_be_viewed_and_cleared(client):
    create_run(client, "示教编程的一般步骤是什么？")
    records = client.get(
        "/api/v1/students/student-1/learning-records", headers={"X-User-ID": "student-1"}
    ).json()["items"]
    assert records
    deleted = client.delete(
        "/api/v1/students/student-1/learning-records", headers={"X-User-ID": "student-1"}
    ).json()["deleted"]
    assert deleted >= 1
