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
    parser = argparse.ArgumentParser(description="隔离重放一个 bad case")
    parser.add_argument("bad_case_id")
    args = parser.parse_args()
    service = BadCaseService(Store(settings.database_path), settings)
    result = service.replay(args.bad_case_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
