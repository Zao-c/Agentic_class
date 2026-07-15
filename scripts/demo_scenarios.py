import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import httpx


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def wait_run(client: httpx.Client, run_id: str, user_id: str) -> Dict[str, Any]:
    for _ in range(150):
        response = client.get(
            "/api/v1/runs/%s" % run_id, headers={"X-User-ID": user_id}
        )
        response.raise_for_status()
        result = response.json()
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(0.1)
    raise TimeoutError("Agent 运行超时：%s" % run_id)


def run_chat(
    client: httpx.Client, user_id: str, session_id: str, message: str
) -> Dict[str, Any]:
    response = client.post(
        "/api/v1/chat",
        json={"session_id": session_id, "user_id": user_id, "message": message},
    )
    response.raise_for_status()
    accepted = response.json()
    result = wait_run(client, accepted["run_id"], user_id)
    trace_response = client.get(
        "/api/v1/traces/%s" % result["request_id"],
        headers={"X-User-ID": user_id},
    )
    trace_response.raise_for_status()
    trace = trace_response.json()
    result["trace_event_count"] = len(trace["events"])
    result["tools"] = [item["tool_name"] for item in trace["state"]["tool_history"]]
    return result


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def compact_run(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "request_id": result["request_id"],
        "run_id": result["run_id"],
        "task_type": result["task_type"],
        "status": result["status"],
        "risk_level": result["risk_level"],
        "citation_count": len(result["citations"]),
        "citation_titles": [item["title"] for item in result["citations"]],
        "trace_event_count": result["trace_event_count"],
        "tools": result["tools"],
        "generated_exercise_id": result.get("generated_exercise_id"),
        "answer": result["answer"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="真实调用服务，复现知识问答、故障诊断、拒答和辅导闭环"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default=None)
    parser.add_argument("--keep-learning-data", action="store_true")
    args = parser.parse_args()
    suffix = uuid.uuid4().hex[:8]
    user_id = "demo-student-%s" % suffix
    session_id = "demo-session-%s" % suffix
    started = datetime.now(timezone.utc)

    with httpx.Client(base_url=args.base_url, timeout=60) as client:
        ready = client.get("/ready")
        ready.raise_for_status()
        ready_payload = ready.json()
        require(ready_payload["status"] == "ready", "服务未就绪")

        knowledge = run_chat(
            client, user_id, session_id + "-qa", "示教编程的一般步骤是什么？"
        )
        require(knowledge["task_type"] == "knowledge_qa", "知识问答路由错误")
        require(knowledge["status"] == "completed", "知识问答未完成")
        require(bool(knowledge["citations"]), "知识问答缺少引用")

        diagnosis = run_chat(
            client,
            user_id,
            session_id + "-diagnosis",
            "ABB IRB120 报警 38213，故障发生在手动模式",
        )
        require(diagnosis["task_type"] == "fault_diagnosis", "故障路由错误")
        require(diagnosis["status"] == "completed", "可控故障诊断未完成")
        require(
            {"lookup_error_code", "manual_retrieval", "check_safety_constraint"}
            <= set(diagnosis["tools"]),
            "故障诊断工具链不完整",
        )

        refusal = run_chat(
            client,
            user_id,
            session_id + "-refusal",
            "量子引力和火星殖民的课程要求是什么？",
        )
        require(refusal["status"] == "abstained", "资料缺失问题没有拒答")

        tutoring = run_chat(
            client,
            user_id,
            session_id + "-tutoring",
            "给我出一道示教编程练习",
        )
        exercise_id = tutoring.get("generated_exercise_id")
        require(tutoring["task_type"] == "tutoring", "辅导路由错误")
        require(bool(exercise_id), "辅导没有创建练习实例")
        answer = (
            "先根据任务要求明确动作和路径及工艺目标，检查设备和现场安全条件；"
            "在手动模式下记录点位，设置运动方式、速度、工具坐标和工件坐标；"
            "编写程序逻辑，低速或单步试运行并检查轨迹，调整点位，确认无误后正式运行。"
        )
        graded_response = client.post(
            "/api/v1/exercises/%s/submit" % exercise_id,
            headers={"X-User-ID": user_id},
            json={"answer": answer},
        )
        graded_response.raise_for_status()
        graded = graded_response.json()
        require(graded["score"] == 100.0, "辅导演示答案未得到预期分数")
        require(graded["mastery"]["status"] == "proficient", "掌握度未更新")
        progress = client.get(
            "/api/v1/students/%s/progress" % user_id,
            headers={"X-User-ID": user_id},
        )
        progress.raise_for_status()
        point = next(
            item
            for item in progress.json()["items"]
            if item["knowledge_point_id"] == "kp_teach_programming_steps"
        )
        class_summary = client.get(
            "/api/v1/classes/progress-summary", headers={"X-Role": "teacher"}
        )
        class_summary.raise_for_status()
        require(user_id not in class_summary.text, "班级聚合泄露了学生标识")

        report = {
            "schema_version": "1.0.0",
            "demo_run_id": "demo_%s" % started.strftime("%Y%m%dT%H%M%SZ"),
            "base_url": args.base_url,
            "ready": {
                "indexed_chunks": ready_payload["indexed_chunks"],
                "alarm_codes": ready_payload["alarm_codes"]["active_records"],
                "knowledge_points": ready_payload["knowledge_points"]["active_records"],
                "retrieval_strategy": ready_payload["retrieval"]["strategy"],
            },
            "scenarios": {
                "knowledge_qa": compact_run(knowledge),
                "fault_diagnosis": compact_run(diagnosis),
                "insufficient_evidence": compact_run(refusal),
                "tutoring": {
                    **compact_run(tutoring),
                    "grading": {
                        "score": graded["score"],
                        "matched_points": graded["matched_points"],
                        "missing_points": graded["missing_points"],
                        "mastery": graded["mastery"],
                    },
                    "progress": point,
                    "class_summary_privacy": class_summary.json()["privacy"],
                },
            },
            "passed": True,
            "created_at": started.isoformat(),
        }
        if not args.keep_learning_data:
            cleanup = client.delete(
                "/api/v1/students/%s/learning-records" % user_id,
                headers={"X-User-ID": user_id},
            )
            cleanup.raise_for_status()
            report["cleanup"] = cleanup.json()

    output = (
        Path(args.output)
        if args.output
        else PROJECT_ROOT / "reports" / (report["demo_run_id"] + ".json")
    )
    if not output.is_absolute():
        output = (PROJECT_ROOT / output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "demo_run_id": report["demo_run_id"],
                "passed": report["passed"],
                "scenario_count": len(report["scenarios"]),
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("演示验收失败：%s" % exc, file=sys.stderr)
        raise SystemExit(1)
