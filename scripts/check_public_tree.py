#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
TEXT_SUFFIXES = {
    "",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

# Split sensitive literals so this checker does not flag its own source.
FORBIDDEN = {
    "private Unix home path": "/" + "home/",
    "private macOS home path": "/" + "Users/",
    "private key material": "BEGIN " + "PRIVATE KEY",
    "GitHub classic token": "gh" + "p_",
    "GitHub fine-grained token": "github_" + "pat_",
    "OpenAI-style token": "s" + "k-",
    "Slack-style token": "xo" + "xb-",
}
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)


def candidate_files() -> list[Path]:
    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode() for item in completed.stdout.split(b"\0") if item]


def main() -> int:
    findings: list[str] = []
    for path in candidate_files():
        if path.resolve() == SELF or path.suffix not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = path.relative_to(ROOT).as_posix()
        for label, marker in FORBIDDEN.items():
            if marker in text:
                findings.append(f"{relative}: contains {label}")
        if EMAIL_RE.search(text):
            findings.append(f"{relative}: contains an email address")

    if findings:
        print("public tree check failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("public tree check ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
