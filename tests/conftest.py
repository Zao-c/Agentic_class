import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.runtime_dirs import isolated_directory


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def tmp_path():
    """Use an ACL-inheriting workspace directory instead of pytest's mode-700 Windows temp."""
    with isolated_directory(PROJECT_ROOT / "runtime" / "test-runs", "pytest-") as path:
        yield path


@pytest.fixture()
def client(tmp_path: Path):
    knowledge = tmp_path / "knowledge"
    evaluation = tmp_path / "eval"
    reports = tmp_path / "reports"
    alarms = tmp_path / "alarm_codes.json"
    knowledge.mkdir()
    evaluation.mkdir()
    (knowledge / "示教编程基本步骤.txt").write_text(
        """## 主题：示教编程基本步骤
示教编程的一般步骤如下：
1. 根据任务要求明确机器人需要完成的动作和路径。
2. 检查机器人本体、末端工具和现场安全条件。
3. 在手动模式下使用示教器移动机器人并记录关键位置。
4. 设置运动方式、速度、工具坐标和工件坐标。
5. 编写程序逻辑并采用低速、单步方式试运行。
6. 检查轨迹并调整点位，确认无误后正式运行。
调试阶段应优先保证安全。
""",
        encoding="utf-8",
    )
    (evaluation / "cases.csv").write_text(
        "id,category,question,expected_points\nT1,流程类,示教编程的一般步骤是什么？,手动模式 低速 单步\n",
        encoding="utf-8",
    )
    project_root = PROJECT_ROOT
    (evaluation / "tutoring_eval_v1.json").write_text(
        (project_root / "data" / "eval" / "tutoring_eval_v1.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    alarms.write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "records": [
                    {
                        "equipment_brand": "ABB",
                        "equipment_models": ["IRB120"],
                        "code": "38213",
                        "title": "机器人电池电量低",
                        "meaning": "测试资料确认该报警表示机器人备份电池电量低。",
                        "likely_causes": ["机器人备份电池电量低"],
                        "safe_checks": ["记录完整报警原文", "查看事件日志"],
                        "forbidden_actions": ["不要自行打开控制柜或更换电池"],
                        "risk_level": "medium",
                        "source_title": "教师确认的 ABB IRB120 报警码测试表",
                        "source_locator": "38213",
                        "review_status": "teacher_confirmed",
                    },
                    {
                        "equipment_brand": "ABB",
                        "equipment_models": ["IRB120"],
                        "code": "10036",
                        "title": "转数计数器需要更新",
                        "meaning": "测试资料确认该报警涉及转数计数器更新。",
                        "likely_causes": ["断电状态下关节发生移动"],
                        "safe_checks": ["记录当前轴状态"],
                        "forbidden_actions": ["不要自行执行校准"],
                        "risk_level": "high",
                        "source_title": "教师确认的 ABB IRB120 报警码测试表",
                        "source_locator": "10036",
                        "review_status": "teacher_confirmed",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    settings = Settings(
        database_path=tmp_path / "test.db",
        knowledge_root=knowledge,
        evaluation_root=evaluation,
        alarm_code_data_path=alarms,
        knowledge_point_data_path=(
            project_root / "data" / "structured" / "knowledge_points_v1.json"
        ),
        reports_root=reports,
        auto_ingest=True,
        ingest_binary_documents=False,
        evidence_threshold=0.16,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def create_run(client: TestClient, message: str, user_id: str = "student-1", session_id: str = "session-1"):
    response = client.post(
        "/api/v1/chat",
        json={"session_id": session_id, "user_id": user_id, "message": message},
    )
    assert response.status_code == 202, response.text
    return response.json()
