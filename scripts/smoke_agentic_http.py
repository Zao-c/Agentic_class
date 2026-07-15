import argparse
import getpass
import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="通过真实 HTTP 与真实模型执行受控 Agent 烟测")
    parser.add_argument(
        "--message",
        default="ABB IRB120 报警 38213，故障发生在手动模式",
    )
    parser.add_argument(
        "--profile",
        choices=["agentic-online", "agentic-quality"],
        default="agentic-online",
    )
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--structured-method", default="json_mode")
    parser.add_argument("--thinking-mode", default="disabled")
    parser.add_argument("--input-cost", type=float, default=0.14)
    parser.add_argument("--output-cost", type=float, default=0.28)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    from scripts.run_profile import load_profile

    load_profile(args.profile)
    os.environ.update(
        {
            "LLM_PROVIDER": args.provider,
            "LLM_MODEL": args.model,
            "LLM_BASE_URL": args.base_url,
            "LLM_STRUCTURED_OUTPUT_METHOD": args.structured_method,
            "LLM_THINKING_MODE": args.thinking_mode,
            "LLM_INPUT_COST_PER_MILLION": str(args.input_cost),
            "LLM_OUTPUT_COST_PER_MILLION": str(args.output_cost),
        }
    )
    key_env = os.environ.get("LLM_API_KEY_ENV", "OPENAI_API_KEY")
    os.environ[key_env] = getpass.getpass("API key（不会回显或写入文件）: ")

    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.decision_provider import build_decision_provider
    from app.main import create_app
    from app.runtime_dirs import isolated_directory

    settings = Settings()
    provider = build_decision_provider(settings)
    if provider is None:  # pragma: no cover
        raise RuntimeError("未构建模型 provider")
    with isolated_directory(PROJECT_ROOT / "runtime" / "smoke-runs", "agentic-http-") as temporary:
        smoke_settings = replace(
            settings,
            database_path=temporary / "smoke.db",
            reports_root=temporary / "reports",
            auto_ingest=True,
            ingest_binary_documents=False,
        )
        with TestClient(
            create_app(smoke_settings, decision_provider_override=provider)
        ) as client:
            ready = client.get("/ready").json()
            accepted = client.post(
                "/api/v1/chat",
                json={
                    "session_id": "agentic-live-smoke",
                    "user_id": "smoke-student",
                    "message": args.message,
                },
            ).json()
            run = client.get(
                "/api/v1/runs/%s" % accepted["run_id"],
                headers={"X-User-ID": "smoke-student"},
            ).json()
            trace = client.get(
                "/api/v1/traces/%s" % accepted["request_id"],
                headers={"X-Role": "maintainer"},
            ).json()

    state = trace["state"]
    summary = {
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "profile": ready["agent"],
        "indexed_chunks": ready["indexed_chunks"],
        "request_id": accepted["request_id"],
        "task_type": run["task_type"],
        "status": run["status"],
        "risk_level": run["risk_level"],
        "stop_reason": state.get("stop_reason"),
        "collected_slots": run["collected_slots"],
        "citation_titles": [item["title"] for item in run["citations"]],
        "decisions": [
            {
                "node": item.get("node"),
                "schema_name": item.get("schema_name"),
                "usage": item.get("usage"),
                "duration_ms": item.get("duration_ms"),
                "attempts": item.get("attempts"),
                "validation_result": item.get("validation_result"),
                "fallback_used": item.get("fallback_used"),
            }
            for item in state.get("decision_history", [])
        ],
        "tools": [
            {
                "tool_name": item["tool_name"],
                "status": item["status"],
                "attempts": item["attempts"],
                "duration_ms": item["duration_ms"],
            }
            for item in state.get("tool_history", [])
        ],
        "model_usage": state.get("model_usage", {}),
        "trace_schema_version": trace["schema_version"],
    }
    report_path = args.report or (
        PROJECT_ROOT
        / "reports"
        / ("agentic_http_smoke_%s.json" % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary["report"] = str(report_path.resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
