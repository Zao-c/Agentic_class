import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.bad_cases import BadCaseService
from app.config import settings
from app.storage import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="运行已晋升的 bad case 回归集")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    service = BadCaseService(Store(settings.database_path), settings)
    report = service.run_regressions(args.limit)
    print(
        json.dumps(
            {
                "regression_run_id": report["regression_run_id"],
                "case_count": report["case_count"],
                "passed": report["passed"],
                "failed": report["failed"],
                "pass_rate": report["pass_rate"],
                "report_files": report["report_files"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(0 if report["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
