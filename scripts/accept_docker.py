from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def command(args: list[str], timeout: int = 1200) -> tuple[str, float]:
    started = time.perf_counter()
    result = subprocess.run(
        args,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    if result.returncode:
        tail = "\n".join((result.stdout + "\n" + result.stderr).splitlines()[-40:])
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(args)}\n{tail}")
    return result.stdout.strip(), duration_ms


def wait_ready(client: httpx.Client, timeout_seconds: float = 90) -> tuple[dict, float]:
    started = time.perf_counter()
    deadline = time.monotonic() + timeout_seconds
    last_error = "not started"
    while time.monotonic() < deadline:
        try:
            response = client.get("/ready")
            if response.status_code == 200 and response.json().get("status") == "ready":
                return response.json(), round((time.perf_counter() - started) * 1000, 2)
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:  # service startup is expected to refuse briefly
            last_error = type(exc).__name__
        time.sleep(1)
    raise TimeoutError(f"/ready did not pass within {timeout_seconds}s: {last_error}")


def wait_run(client: httpx.Client, run_id: str, user_id: str) -> dict:
    for _ in range(300):
        response = client.get(f"/api/v1/runs/{run_id}", headers={"X-User-ID": user_id})
        response.raise_for_status()
        payload = response.json()
        if payload["status"] not in {"queued", "running"}:
            return payload
        time.sleep(0.1)
    raise TimeoutError(f"run did not finish: {run_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and accept Docker Compose on a real Docker host")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--keep-running", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    started_at = datetime.now(timezone.utc)
    report: dict = {
        "schema_version": "1.0.0",
        "started_at": started_at.isoformat(),
        "machine": {"platform": platform.platform(), "python": platform.python_version()},
        "checks": {},
        "passed": False,
    }
    output = args.output or PROJECT_ROOT / "reports" / f"docker_acceptance_{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    try:
        docker_version, _ = command(["docker", "version", "--format", "{{json .Server.Version}}"], 30)
        compose_version, _ = command(["docker", "compose", "version", "--short"], 30)
        report["docker"] = {"engine": docker_version.strip('"'), "compose": compose_version}

        _, build_ms = command(["docker", "compose", "build"])
        report["checks"]["build"] = {"passed": True, "duration_ms": build_ms}
        _, up_ms = command(["docker", "compose", "up", "-d"])
        report["checks"]["compose_up"] = {"passed": True, "duration_ms": up_ms}

        with httpx.Client(base_url=args.base_url, timeout=30) as client:
            ready, ready_ms = wait_ready(client)
            health = client.get("/health")
            health.raise_for_status()
            report["checks"]["health_ready"] = {
                "passed": health.json().get("status") == "ok",
                "startup_to_ready_ms": ready_ms,
                "indexed_chunks": ready.get("indexed_chunks"),
            }

            demo_output = PROJECT_ROOT / "reports" / f"docker_demo_{uuid.uuid4().hex[:8]}.json"
            _, demo_ms = command([
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "demo_scenarios.py"),
                "--base-url", args.base_url,
                "--output", str(demo_output),
            ], 300)
            demo = json.loads(demo_output.read_text(encoding="utf-8"))
            report["checks"]["three_tasks"] = {
                "passed": bool(demo.get("passed")),
                "duration_ms": demo_ms,
                "task_types": sorted({
                    item.get("task_type")
                    for item in demo.get("scenarios", {}).values()
                    if isinstance(item, dict) and item.get("task_type")
                }),
                "demo_report": demo_output.relative_to(PROJECT_ROOT).as_posix(),
            }

            suffix = uuid.uuid4().hex[:8]
            user_id = f"docker-persistence-{suffix}"
            accepted = client.post(
                "/api/v1/chat",
                json={
                    "session_id": f"docker-persistence-{suffix}",
                    "user_id": user_id,
                    "message": "工业机器人课程中的坐标系有什么区别？",
                },
            )
            accepted.raise_for_status()
            before = wait_run(client, accepted.json()["run_id"], user_id)

        database = PROJECT_ROOT / "runtime" / "robot_tutor.db"
        size_before = database.stat().st_size if database.exists() else 0
        _, restart_ms = command(["docker", "compose", "restart"])
        with httpx.Client(base_url=args.base_url, timeout=30) as client:
            _, restart_ready_ms = wait_ready(client)
            restored = client.get(
                f"/api/v1/runs/{before['run_id']}", headers={"X-User-ID": user_id}
            )
            restored.raise_for_status()
            restored_payload = restored.json()
        report["checks"]["persistent_restart"] = {
            "passed": restored_payload["request_id"] == before["request_id"] and size_before > 0,
            "restart_ms": restart_ms,
            "restart_to_ready_ms": restart_ready_ms,
            "database_bytes_before_restart": size_before,
            "restored_status": restored_payload["status"],
        }

        container_id, _ = command(["docker", "compose", "ps", "-q", "robot-tutor"], 30)
        image_id, _ = command([
            "docker", "inspect", "--format", "{{.Image}}", container_id,
        ], 30)
        report["image_id"] = image_id
        report["passed"] = all(item.get("passed") for item in report["checks"].values())
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not args.keep_running:
            try:
                subprocess.run(
                    ["docker", "compose", "down"], cwd=PROJECT_ROOT,
                    capture_output=True, check=False,
                )
            except OSError:
                pass
    print(json.dumps({"passed": report["passed"], "report": str(output)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
