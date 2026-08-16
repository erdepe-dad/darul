"""Runtime configuration and repository identity discovery."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


DEFAULT_URI = "bolt://127.0.0.1:7687"
DEFAULT_EXCLUDES = frozenset(
    {
        ".git",
        ".graph_engine",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".tox",
        ".venv",
        "build",
        "coverage",
        "dist",
        "dist.old",
        "node_modules",
        "target",
        "vendor",
        "venv",
    }
)


def _load_local_env(root: Path) -> None:
    """Load project-local graph credentials without overriding the process environment."""
    for path in (root / ".env", root / ".graph_engine" / "neo4j.env"):
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def get_repo_root(start: Path | None = None) -> Path:
    """Return the Git root, falling back to the active directory."""
    active = (start or Path.cwd()).resolve()
    try:
        result = subprocess.run(
            ["git", "-C", str(active), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
        return Path(result.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        return active


def get_repo_name(root: Path) -> str:
    return os.getenv("CURRENT_REPO_NAME", root.name).strip() or root.name


@dataclass(frozen=True, slots=True)
class Settings:
    repo_root: Path
    repo_name: str
    uri: str
    user: str
    password: str
    database: str | None
    connection_timeout: float
    excludes: frozenset[str]

    @property
    def auth(self) -> tuple[str, str]:
        return self.user, self.password


def load_settings(repo_root: Path | None = None) -> Settings:
    root = get_repo_root(repo_root)
    _load_local_env(root)
    extra_excludes = {
        item.strip()
        for item in os.getenv("GRAPH_ENGINE_EXCLUDES", "").split(",")
        if item.strip()
    }
    return Settings(
        repo_root=root,
        repo_name=get_repo_name(root),
        uri=os.getenv("GRAPH_DB_URI", DEFAULT_URI),
        user=os.getenv("GRAPH_DB_USER", "neo4j"),
        password=os.getenv("GRAPH_DB_PASS", ""),
        database=os.getenv("GRAPH_DB_DATABASE") or None,
        connection_timeout=float(os.getenv("GRAPH_DB_TIMEOUT", "3")),
        excludes=DEFAULT_EXCLUDES | extra_excludes,
    )


SETTINGS = load_settings()
REPO_ROOT = SETTINGS.repo_root
REPO_NAME = SETTINGS.repo_name
URI = SETTINGS.uri
PASSWORD = SETTINGS.password
AUTH = SETTINGS.auth
