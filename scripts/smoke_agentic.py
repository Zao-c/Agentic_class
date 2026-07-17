import argparse
import getpass
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

def main() -> None:
    parser = argparse.ArgumentParser(description="执行一次真实受控 LLM 决策烟测")
    parser.add_argument(
        "--message",
        default="ABB IRB120 报警 38213，故障发生在手动模式",
    )
    parser.add_argument("--profile", choices=["agentic-online", "agentic-quality"], default="agentic-online")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument(
        "--structured-method",
        choices=["json_schema", "json_mode", "function_calling"],
    )
    parser.add_argument("--thinking-mode", choices=["enabled", "disabled"])
    parser.add_argument("--input-cost", type=float)
    parser.add_argument("--output-cost", type=float)
    parser.add_argument(
        "--prompt-key",
        action="store_true",
        help="从无回显终端提示读取密钥，只保存在当前进程环境中",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    from scripts.run_profile import load_profile

    load_profile(args.profile)
    overrides = {
        "LLM_PROVIDER": args.provider,
        "LLM_MODEL": args.model,
        "LLM_BASE_URL": args.base_url,
        "LLM_STRUCTURED_OUTPUT_METHOD": args.structured_method,
        "LLM_THINKING_MODE": args.thinking_mode,
        "LLM_INPUT_COST_PER_MILLION": (
            str(args.input_cost) if args.input_cost is not None else None
        ),
        "LLM_OUTPUT_COST_PER_MILLION": (
            str(args.output_cost) if args.output_cost is not None else None
        ),
    }
    for name, value in overrides.items():
        if value is not None:
            os.environ[name] = value
    if args.prompt_key:
        key_env = os.environ.get("LLM_API_KEY_ENV", "OPENAI_API_KEY")
        os.environ[key_env] = getpass.getpass("API key（不会回显或写入文件）: ")
    from app.agentic_graph import ControlledAgentGraph
    from app.config import Settings
    from app.decision_provider import build_decision_provider
    from app.workflow import classify_intent, extract_diagnostic_slots

    settings = Settings()
    provider = build_decision_provider(settings)
    if provider is None:  # pragma: no cover
        raise RuntimeError("未构建模型 provider")
    graph = ControlledAgentGraph(provider, settings)
    result = graph.run_preflight(
        args.message,
        history=[],
        deterministic_slots=extract_diagnostic_slots(args.message),
        deterministic_task=classify_intent(args.message),
    )
    summary = {
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "profile": args.profile,
        "provider": provider.provider_name,
        "model": provider.model_name,
        "task_type": result.get("task_type"),
        "normalized_query": result.get("normalized_query"),
        "collected_slots": result.get("collected_slots", {}),
        "missing_slots": result.get("missing_slots", []),
        "validated_tools": result.get("executed_tools", []),
        "decisions": [
            {
                "node": item["node"],
                "schema_name": item["schema_name"],
                "usage": item["usage"],
                "duration_ms": item["duration_ms"],
                "attempts": item["attempts"],
            }
            for item in result.get("decisions", [])
        ],
    }
    summary["totals"] = {
        "input_tokens": sum(item["usage"]["input_tokens"] for item in result.get("decisions", [])),
        "output_tokens": sum(item["usage"]["output_tokens"] for item in result.get("decisions", [])),
        "total_tokens": sum(item["usage"]["total_tokens"] for item in result.get("decisions", [])),
        "estimated_cost_usd": round(
            sum(float(item.get("estimated_cost_usd", 0.0)) for item in result.get("decisions", [])),
            8,
        ),
        "duration_ms": round(
            sum(float(item["duration_ms"]) for item in result.get("decisions", [])), 2
        ),
    }
    report_path = args.report or (
        PROJECT_ROOT
        / "reports"
        / ("agentic_smoke_%s.json" % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary["report"] = str(report_path.resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
