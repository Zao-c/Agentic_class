import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="调用已启动的智能助教服务完成一次演示")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--message", default="示教编程的一般步骤是什么？")
    parser.add_argument("--session-id", default="demo-session")
    parser.add_argument("--user-id", default="demo-student")
    parser.add_argument("--summary", action="store_true", help="只输出状态、回答和引用标题")
    args = parser.parse_args()
    user_id = args.user_id
    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        created = client.post(
            "/api/v1/chat",
            json={"session_id": args.session_id, "user_id": user_id, "message": args.message},
        )
        created.raise_for_status()
        run = created.json()
        for _ in range(100):
            response = client.get(
                "/api/v1/runs/%s" % run["run_id"], headers={"X-User-ID": user_id}
            )
            response.raise_for_status()
            result = response.json()
            if result["status"] not in {"queued", "running"}:
                if args.summary:
                    result = {
                        "task_type": result["task_type"],
                        "status": result["status"],
                        "answer": result["answer"],
                        "risk_level": result["risk_level"],
                        "citations": [item["title"] for item in result["citations"]],
                    }
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return
            time.sleep(0.1)
    raise SystemExit("演示请求超时")


if __name__ == "__main__":
    main()
