#!/usr/bin/env python3
"""Fail safely when public-release files contain likely secrets or local artifacts."""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOCAL_PATTERNS = (
    ".env",
    ".env.*",
    ".graph_engine/*.env",
    ".venv/*",
    "**/__pycache__/*",
    ".impeccable/questions/*",
    ".impeccable/references/*",
    ".impeccable/screenshots/*",
    ".agents/*",
    ".codex/*",
)
ALLOWLIST = {".env.example", ".graph_engine/neo4j.env.example"}
REQUIRED_FILES = {"README.md", "SECURITY.md", "CONTRIBUTING.md", "LICENSE"}
SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "authorization bearer": re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+[A-Za-z0-9._-]{16,}"),
    "assigned quoted secret": re.compile(
        r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|client[_-]?secret)\s*[=:]\s*['\"](?!replace|example|changeme|your-|\$\{|<)[^'\"]{12,}['\"]"
    ),
}


def _tracked_files() -> list[Path] | None:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        return None
    files = [ROOT / item.decode() for item in result.stdout.split(b"\0") if item]
    return files or None


def _is_local(path: Path) -> bool:
    relative = path.relative_to(ROOT).as_posix()
    if relative in ALLOWLIST:
        return False
    return any(fnmatch.fnmatch(relative, pattern) for pattern in LOCAL_PATTERNS)


def _candidate_files() -> tuple[list[Path], str]:
    tracked = _tracked_files()
    if tracked is not None:
        return tracked, "tracked files"
    files = [path for path in ROOT.rglob("*") if path.is_file() and not _is_local(path)]
    return files, "public candidates (Git repository not initialized)"


def audit() -> list[str]:
    files, scope = _candidate_files()
    findings: list[str] = []
    relative_files = {path.relative_to(ROOT).as_posix() for path in files}
    for required in sorted(REQUIRED_FILES - relative_files):
        findings.append(f"missing required public file: {required}")

    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        if _is_local(path):
            findings.append(f"local-only artifact is included in {scope}: {relative}")
            continue
        if path.stat().st_size > 5 * 1024 * 1024:
            findings.append(f"large file over 5 MiB: {relative}")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(content):
                findings.append(f"possible {label}: {relative}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    findings = audit()
    if findings:
        print("Public release audit failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("Public release audit passed: no likely credentials or local-only artifacts found.")
    local_env = ROOT / ".graph_engine" / "neo4j.env"
    if local_env.exists():
        print("Local credential file remains present but excluded from the public candidate set.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
