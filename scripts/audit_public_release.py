from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


TEXT_SUFFIXES = {
    ".css", ".csv", ".env", ".html", ".ini", ".js", ".json", ".jsonl",
    ".md", ".py", ".toml", ".txt", ".xml", ".yaml", ".yml",
}
LOCAL_ONLY_PREFIXES = (
    "data/active/", "data/candidate/", "data/excluded/", "data/archive/",
    "data/indexes/", "data/processed/", "runtime/",
)
LOCAL_ONLY_FILES = {"01-工业机器人课程智能助教-Agent-平台任务书.md", ".env"}
PUBLIC_SCAN_ROOTS = (
    "app", "configs", "data/datasets", "data/eval", "data/public_sample",
    "data/structured", "docs", "reports", "scripts", "tests", ".github",
)
TOP_LEVEL_PUBLIC = {
    ".dockerignore", ".env.example", ".gitignore", "CHANGELOG.md",
    "Dockerfile", "LICENSE", "README.md", "docker-compose.yml",
    "pyproject.toml", "requirements-agentic.txt", "requirements-dev.txt",
    "requirements-neural.txt", "requirements.txt",
}
SECRET_RULES = {
    "api_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github_token": re.compile(r"\bgh[opurs]_[A-Za-z0-9]{30,}\b"),
}
ABSOLUTE_PATH = re.compile(r"(?:\b[A-Za-z]:\\|/(?:Users|home)/[^\s\"']+)")
EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
MOBILE_CN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ALLOW_DIRECTIVE = re.compile(r"public-audit:\s*allow=([a-z0-9_, -]+)", re.I)


@dataclass(frozen=True)
class Finding:
    severity: str
    rule: str
    path: str
    line: int | None = None


def _git_files(root: Path) -> list[Path] | None:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return [root / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def _fallback_files(root: Path) -> list[Path]:
    files = [root / name for name in TOP_LEVEL_PUBLIC if (root / name).is_file()]
    for name in PUBLIC_SCAN_ROOTS:
        location = root / name
        if location.exists():
            files.extend(path for path in location.rglob("*") if path.is_file())
    ignored_parts = {"__pycache__", ".pytest_cache"}
    return sorted({path for path in files if not ignored_parts.intersection(path.parts)})


def public_files(root: Path) -> list[Path]:
    return _git_files(root) or _fallback_files(root)


def audit(root: Path, files: list[Path] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for path in files or public_files(root):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in LOCAL_ONLY_FILES or relative.startswith(LOCAL_ONLY_PREFIXES):
            findings.append(Finding("error", "local_only_file_tracked", relative))
        if path.stat().st_size > 10 * 1024 * 1024:
            findings.append(Finding("error", "large_file_over_10mb", relative))
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in TOP_LEVEL_PUBLIC:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            directive = ALLOW_DIRECTIVE.search(line)
            allowed = {
                item.strip().lower()
                for item in (directive.group(1).split(",") if directive else [])
                if item.strip()
            }
            for rule, pattern in SECRET_RULES.items():
                if pattern.search(line) and rule not in allowed:
                    findings.append(Finding("error", rule, relative, number))
            if ABSOLUTE_PATH.search(line) and "local_absolute_path" not in allowed:
                findings.append(Finding("warning", "local_absolute_path", relative, number))
            if EMAIL.search(line) and "email_address" not in allowed:
                findings.append(Finding("warning", "email_address", relative, number))
            if MOBILE_CN.search(line) and "china_mobile_number" not in allowed:
                findings.append(Finding("warning", "china_mobile_number", relative, number))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit files intended for a public repository")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--strict", action="store_true", help="fail on warnings as well as errors")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    files = public_files(root)
    findings = audit(root, files)
    payload = {
        "root": str(root),
        "files_scanned": len(files),
        "errors": sum(item.severity == "error" for item in findings),
        "warnings": sum(item.severity == "warning" for item in findings),
        "findings": [asdict(item) for item in findings],
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    blocked = payload["errors"] or (args.strict and payload["warnings"])
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
