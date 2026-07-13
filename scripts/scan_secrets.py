#!/usr/bin/env python3
"""Fail when tracked source contains a high-confidence credential literal."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 5 * 1024 * 1024
KNOWN_SECRET_PATTERNS = (
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("github-token", re.compile(r"gh[pousr]_[0-9A-Za-z]{36,255}")),
    (
        "private-key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
)
ASSIGNED_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|client[_-]?secret|password|access[_-]?token)\b"
    r"\s*[:=]\s*([\"'])([^\"'\r\n]{8,})\2"
)
SAFE_LITERAL_MARKERS = (
    "<redacted>",
    "<redacted-url>",
    "placeholder",
    "your_",
    "example",
    "fixture",
    "test-secret",
    "audit_secret_value",
)


def repository_files(root: Path = ROOT) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [root / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def scan_paths(paths: list[Path], *, root: Path = ROOT) -> list[dict]:
    findings = []
    for path in paths:
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if len(raw) > MAX_FILE_BYTES or b"\0" in raw:
            continue
        text = raw.decode("utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for rule, pattern in KNOWN_SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append({
                        "path": str(path.relative_to(root)).replace("\\", "/"),
                        "line": line_number,
                        "rule": rule,
                    })
            assigned = ASSIGNED_SECRET.search(line)
            if assigned:
                value = assigned.group(3).strip().lower()
                if not any(marker in value for marker in SAFE_LITERAL_MARKERS):
                    findings.append({
                        "path": str(path.relative_to(root)).replace("\\", "/"),
                        "line": line_number,
                        "rule": "literal-secret-assignment",
                    })
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = parser.parse_args()
    findings = scan_paths(repository_files())
    payload = {
        "schema_version": 1,
        "scanned": "tracked-and-untracked-nonignored-files",
        "findings": findings,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    elif findings:
        for finding in findings:
            print(f"{finding['path']}:{finding['line']}: {finding['rule']}")
    else:
        print("No high-confidence credential literals found")
    raise SystemExit(1 if findings else 0)


if __name__ == "__main__":
    main()
