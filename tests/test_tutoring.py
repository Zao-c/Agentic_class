from tests.conftest import create_run


FULL_ANSWER = (
    "先根据任务要求明确动作和路径及工艺目标，检查设备和现场安全条件；"
    "在手动模式下用示教器记录点位，设置运动方式、速度、工具坐标和工件坐标；"
    "编写程序逻辑，然后低速或单步试运行并检查轨迹，调整点位，确认无误后正式运行。"
)


def test_agent_generates_persisted_source_grounded_exercise(client):
    run = create_run(client, "给我出一道示教编程练习")
    result = client.get(
        "/api/v1/runs/%s" % run["run_id"],
        headers={"X-User-ID": "student-1"},
    ).json()
    assert result["status"] == "completed"
    assert result["task_type"] == "tutoring"
    assert result["generated_exercise_id"].startswith("ex_")
    assert result["citations"]
    assert result["generated_exercise_id"] in result["answer"]

    exercises = client.get(
        "/api/v1/students/student-1/exercises",
        headers={"X-User-ID": "student-1"},
    ).json()["items"]
    assert exercises[0]["exercise_id"] == result["generated_exercise_id"]
    assert "criteria" not in exercises[0]


def test_exercise_grading_updates_explainable_mastery(client):
    created = client.post(
        "/api/v1/students/student-1/exercises",
        headers={"X-User-ID": "student-1"},
        json={"knowledge_point_id": "kp_teach_programming_steps"},
    )
    assert created.status_code == 201, created.text
    exercise = created.json()
    assert "criteria" not in exercise
    assert exercise["citation"]["chunk_id"]

    graded = client.post(
        "/api/v1/exercises/%s/submit" % exercise["exercise_id"],
        headers={"X-User-ID": "student-1"},
        json={"answer": FULL_ANSWER},
    )
    assert graded.status_code == 200, graded.text
    result = graded.json()
    assert result["score"] == 100.0
    assert result["missing_points"] == []
    assert all(item["matched"] for item in result["criterion_results"])
    assert result["mastery"]["status"] == "proficient"
    assert result["citation"]["title"]

    progress = client.get(
        "/api/v1/students/student-1/progress",
        headers={"X-User-ID": "student-1"},
    ).json()["items"]
    point = next(
        item for item in progress
        if item["knowledge_point_id"] == "kp_teach_programming_steps"
    )
    assert point["attempts"] == 1
    assert point["mastery_score"] == 100.0
    assert point["mastery_status"] == "proficient"


def test_tutoring_authorization_aggregate_and_deletion(client):
    exercise = client.post(
        "/api/v1/students/student-1/exercises",
        headers={"X-User-ID": "student-1"},
        json={"knowledge_point_id": "kp_teach_programming_steps"},
    ).json()
    denied = client.post(
        "/api/v1/exercises/%s/submit" % exercise["exercise_id"],
        headers={"X-User-ID": "student-2"},
        json={"answer": FULL_ANSWER},
    )
    assert denied.status_code == 403
    assert client.get(
        "/api/v1/students/student-1/progress",
        headers={"X-User-ID": "student-2"},
    ).status_code == 403

    client.post(
        "/api/v1/exercises/%s/submit" % exercise["exercise_id"],
        headers={"X-User-ID": "student-1"},
        json={"answer": "在手动模式下低速运行。"},
    )
    summary = client.get(
        "/api/v1/classes/progress-summary",
        headers={"X-Role": "teacher"},
    )
    assert summary.status_code == 200
    assert summary.json()["privacy"] == "knowledge_point_aggregate_without_user_id"
    assert "student-1" not in summary.text
    assert client.get("/api/v1/classes/progress-summary").status_code == 422

    deleted = client.delete(
        "/api/v1/students/student-1/learning-records",
        headers={"X-User-ID": "student-1"},
    ).json()["deleted"]
    assert deleted >= 3
    assert client.get(
        "/api/v1/students/student-1/exercises",
        headers={"X-User-ID": "student-1"},
    ).json()["items"] == []
    progress = client.get(
        "/api/v1/students/student-1/progress",
        headers={"X-User-ID": "student-1"},
    ).json()["items"]
    assert all(item["mastery_status"] == "not_started" for item in progress)


def test_student_knowledge_point_view_hides_grading_criteria(client):
    student_view = client.get("/api/v1/knowledge/points").json()["items"]
    teacher_view = client.get(
        "/api/v1/knowledge/points", headers={"X-Role": "teacher"}
    ).json()["items"]
    assert student_view
    assert "criteria" not in student_view[0]
    assert teacher_view[0]["criteria"]


def test_tutoring_evaluation_runs_isolated_closed_loop(client):
    response = client.post(
        "/api/v1/evaluations/tutoring",
        headers={"X-Role": "maintainer"},
        json={},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["metrics"]["case_count"] == 4
    assert report["metrics"]["score_range_accuracy"] == 1.0
    assert report["metrics"]["mastery_status_accuracy"] == 1.0
    assert report["metrics"]["source_traceability_rate"] == 1.0
    assert report["metrics"]["progress_update_rate"] == 1.0
    assert len(report["report_files"]) == 2
