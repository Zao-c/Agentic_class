import argparse
import os
import sys
from pathlib import Path
from typing import Dict, MutableMapping, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILE_FILES = {
    "portable": PROJECT_ROOT / "configs" / "portable.env",
    "agentic-online": PROJECT_ROOT / "configs" / "agentic-online.env",
    "agentic-quality": PROJECT_ROOT / "configs" / "agentic-quality.env",
    "neural-online": PROJECT_ROOT / "configs" / "neural-online.env",
    "neural-quality": PROJECT_ROOT / "configs" / "neural-quality.env",
}


def parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("%s:%d 不是有效的 KEY=VALUE 配置" % (path, line_number))
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("%s:%d 的配置键为空" % (path, line_number))
        values[key] = value.strip()
    return values


def load_profile(
    profile: str, environ: Optional[MutableMapping[str, str]] = None
) -> Dict[str, str]:
    path = PROFILE_FILES[profile]
    values = parse_env_file(path)
    target = os.environ if environ is None else environ
    for key, value in values.items():
        target[key] = value
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description="按 Agent 与检索组合档位启动课程智能助教")
    parser.add_argument("--profile", choices=PROFILE_FILES, default="portable")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    values = load_profile(args.profile)
    os.chdir(PROJECT_ROOT)
    sys.path.insert(0, str(PROJECT_ROOT))

    import uvicorn

    print(
        "启动档位：%s；Agent：%s；检索策略：%s"
        % (args.profile, values.get("AGENT_PROFILE", "portable"), values["RETRIEVAL_STRATEGY"]),
        flush=True,
    )
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
