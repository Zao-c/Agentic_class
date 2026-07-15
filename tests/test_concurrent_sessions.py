from concurrent.futures import ThreadPoolExecutor
import uuid

from app.schemas import AgentState


def test_concurrent_runs_do_not_read_queued_siblings(client):
    store = client.app.state.store
    workflow = client.app.state.workflow
    states = [
        AgentState(
            request_id="req_" + uuid.uuid4().hex,
            run_id="run_" + uuid.uuid4().hex,
            session_id="shared-session",
            user_id="shared-user",
            original_message=message,
        )
        for message in (
            "ABB IRB120 报警 50056，发生在手动模式",
            "示教编程的一般步骤是什么？",
        )
    ]
    for state in states:
        store.create_run(state)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(workflow.run, states))
    assert results[0].task_type.value == "fault_diagnosis"
    assert results[1].task_type.value == "knowledge_qa"
    assert results[1].normalized_query == "示教编程的一般步骤是什么？"


def test_topic_switch_after_diagnosis_does_not_inherit_fault_intent(client):
    first = client.post(
        "/api/v1/chat",
        json={
            "session_id": "topic-switch",
            "user_id": "student-switch",
            "message": "ABB IRB120 报警 50056，发生在手动模式",
        },
    ).json()
    assert client.get(
        "/api/v1/runs/%s" % first["run_id"], headers={"X-User-ID": "student-switch"}
    ).json()["task_type"] == "fault_diagnosis"

    second = client.post(
        "/api/v1/chat",
        json={
            "session_id": "topic-switch",
            "user_id": "student-switch",
            "message": "示教编程的一般步骤是什么？",
        },
    ).json()
    result = client.get(
        "/api/v1/runs/%s" % second["run_id"], headers={"X-User-ID": "student-switch"}
    ).json()
    assert result["task_type"] == "knowledge_qa"
    assert result["status"] == "completed"
