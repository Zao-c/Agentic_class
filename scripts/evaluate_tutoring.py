import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.tutoring_evaluation import TutoringEvaluationService


def main() -> None:
    parser = argparse.ArgumentParser(description="运行个性化辅导端到端闭环评测")
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    report = TutoringEvaluationService(settings).run(args.dataset, args.limit)
    print(
        json.dumps(
            {
                "evaluation_run_id": report["evaluation_run_id"],
                "metrics": report["metrics"],
                "report_files": report["report_files"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
