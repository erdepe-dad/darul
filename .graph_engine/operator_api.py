"""Guarded operator metrics and background repository operations."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import SETTINGS, Settings
from .db import GraphDB, GraphEngineError
from .stitcher import clear_service, configure_service, list_services, stitch_endpoints


REPOSITORY_SUMMARY_QUERY = """
MATCH (r:Repository)
OPTIONAL MATCH (r)-[:CONTAINS]->(f:CodeFile)
RETURN r.name AS name, r.root_path AS root_path, r.updated_at AS updated_at,
       count(DISTINCT f) AS files
ORDER BY toLower(r.name)
"""

REQUEST_SUMMARY_QUERY = """
MATCH (a:APIEndpoint)
OPTIONAL MATCH (a)-[route:TARGETS_ROUTE]->(b:BackendRoute)
RETURN a.repo_name AS name, count(DISTINCT a) AS requests,
       count(DISTINCT CASE WHEN b IS NOT NULL THEN a END) AS candidates,
       count(DISTINCT CASE WHEN route.trust_status = 'VALIDATED' THEN a END) AS resolved
ORDER BY toLower(a.repo_name)
"""

UNRESOLVED_REQUESTS_QUERY = """
MATCH (a:APIEndpoint {repo_name: $repo_name})
WHERE NOT (a)-[:TARGETS_ROUTE {trust_status: 'VALIDATED'}]->(:BackendRoute)
OPTIONAL MATCH (p:Page)-[:MAKES_REQUEST]->(a)
OPTIONAL MATCH (a)-[:TARGETS_SYSTEM]->(s:ExternalSystem)
OPTIONAL MATCH (a)-[candidate:TARGETS_ROUTE]->(b:BackendRoute)
RETURN a.id AS id, a.method AS method, a.url AS url,
       a.normalized_url AS normalized_url, a.line AS line,
       a.source_file_id AS source_file_id, p.route_path AS page_route,
       s.name AS service_key, s.base_url AS base_url, s.target_repo AS target_repo,
       b.repo_name AS candidate_repo, b.route_path AS candidate_route,
       coalesce(candidate.trust_status, 'SUGGESTED') AS candidate_trust_status
ORDER BY toLower(coalesce(a.source_file_id, '')), a.line, a.method, a.normalized_url
LIMIT $limit
"""

_SAFE_REPOSITORY = re.compile(r"^[A-Za-z0-9._-]+$")
_ACTIONS = {"build", "sync"}


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class RepositoryRegistry:
    """Resolve repository names without accepting paths from HTTP requests."""

    roots: dict[str, Path]
    parent: Path

    @classmethod
    def from_settings(cls, settings: Settings = SETTINGS) -> "RepositoryRegistry":
        roots = {settings.repo_name: settings.repo_root.resolve()}
        for item in os.getenv("DARUL_REPO_ROOTS", "").split(","):
            if "=" not in item:
                continue
            name, raw_path = item.split("=", 1)
            clean_name = name.strip()
            if _SAFE_REPOSITORY.fullmatch(clean_name) and raw_path.strip():
                roots[clean_name] = Path(raw_path.strip()).expanduser().resolve()
        parent = Path(os.getenv("DARUL_REPO_PARENT", str(settings.repo_root.parent))).resolve()
        return cls(roots, parent)

    def resolve(self, name: str) -> Path:
        clean_name = name.strip()
        if not _SAFE_REPOSITORY.fullmatch(clean_name):
            raise ValueError("repository name is invalid")
        candidate = self.roots.get(clean_name)
        if candidate is None:
            candidate = (self.parent / clean_name).resolve()
            if candidate.parent != self.parent:
                raise ValueError("repository is outside the configured repository parent")
        if not candidate.is_dir() or not (candidate / ".git").exists():
            raise ValueError(f"repository is not available for operations: {clean_name}")
        return candidate

    def available(self, name: str) -> bool:
        try:
            self.resolve(name)
        except ValueError:
            return False
        return True


class OperatorService:
    def __init__(
        self,
        db: GraphDB,
        settings: Settings = SETTINGS,
        registry: RepositoryRegistry | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.registry = registry or RepositoryRegistry.from_settings(settings)

    def overview(self) -> dict[str, Any]:
        repositories = self.db.execute_read(REPOSITORY_SUMMARY_QUERY)
        metrics = {
            row["name"]: row
            for row in self.db.execute_read(REQUEST_SUMMARY_QUERY)
            if row.get("name")
        }
        rows = []
        for repository in repositories:
            name = str(repository.get("name") or "")
            metric = metrics.get(name, {})
            requests = int(metric.get("requests") or 0)
            resolved = int(metric.get("resolved") or 0)
            candidates = int(metric.get("candidates") or 0)
            rows.append(
                {
                    **repository,
                    "requests": requests,
                    "resolved": resolved,
                    "candidates": candidates,
                    "unresolved": max(0, requests - resolved),
                    "coverage": round((resolved / requests * 100) if requests else 100.0, 1),
                    "operable": self.registry.available(name),
                }
            )
        return {
            "database": self.db.healthcheck(),
            "repositories": rows,
            "services": list_services(self.db),
            "atlas_url": "/",
        }

    def unresolved(self, repo_name: str, limit: int = 100) -> dict[str, Any]:
        clean_name = repo_name.strip()
        if not clean_name:
            raise ValueError("repository is required")
        bounded_limit = max(1, min(int(limit), 500))
        rows = self.db.execute_read(
            UNRESOLVED_REQUESTS_QUERY,
            repo_name=clean_name,
            limit=bounded_limit,
        )
        return {"repository": clean_name, "requests": rows, "limit": bounded_limit}

    def set_service(self, payload: dict[str, Any]) -> dict[str, Any]:
        repo_name = str(payload.get("repo_name") or "").strip()
        if not repo_name:
            raise ValueError("repo_name is required")
        service = configure_service(
            self.db,
            str(payload.get("key") or ""),
            str(payload.get("base_url") or ""),
            repo_name=repo_name,
            target_repo=str(payload.get("target_repo") or ""),
        )
        return {"service": service, "stitched": stitch_endpoints(self.db)}

    def clear_service(self, payload: dict[str, Any]) -> dict[str, Any]:
        repo_name = str(payload.get("repo_name") or "").strip()
        key = str(payload.get("key") or "").strip()
        if not repo_name or not key:
            raise ValueError("repo_name and key are required")
        service = clear_service(self.db, key, repo_name=repo_name)
        return {"service": service, "stitched": stitch_endpoints(self.db)}


class OperationManager:
    def __init__(
        self,
        registry: RepositoryRegistry,
        *,
        engine_root: Path,
        max_workers: int = 1,
        timeout: int = 900,
    ) -> None:
        self.registry = registry
        self.engine_root = engine_root.resolve()
        self.timeout = timeout
        self._executor = ThreadPoolExecutor(max_workers=max(1, min(max_workers, 2)))
        self._lock = threading.Lock()
        self._operations: dict[str, dict[str, Any]] = {}

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = [dict(item) for item in self._operations.values()]
        rows.sort(key=lambda item: item["created_at"], reverse=True)
        return rows[: max(1, min(limit, 100))]

    def start(self, action: str, repo_name: str) -> dict[str, Any]:
        clean_action = action.strip().lower()
        if clean_action not in _ACTIONS:
            raise ValueError("action must be build or sync")
        clean_repo = repo_name.strip()
        root = self.registry.resolve(clean_repo)
        operation_id = uuid.uuid4().hex
        operation = {
            "id": operation_id,
            "action": clean_action,
            "repository": clean_repo,
            "status": "queued",
            "created_at": _timestamp(),
            "started_at": None,
            "finished_at": None,
            "returncode": None,
            "output": "",
        }
        with self._lock:
            self._operations[operation_id] = operation
        self._executor.submit(self._run, operation_id, root)
        return dict(operation)

    def _run(self, operation_id: str, root: Path) -> None:
        with self._lock:
            operation = self._operations[operation_id]
            operation["status"] = "running"
            operation["started_at"] = _timestamp()
            action = operation["action"]
            repo_name = operation["repository"]
        env = os.environ.copy()
        python_path = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(self.engine_root) + (os.pathsep + python_path if python_path else "")
        env["CURRENT_REPO_NAME"] = repo_name
        try:
            result = subprocess.run(
                [sys.executable, "-m", "graph_engine.cli", action],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
            combined = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
            status = "succeeded" if result.returncode == 0 else "failed"
            returncode = result.returncode
        except subprocess.TimeoutExpired as exc:
            combined = f"Operation timed out after {self.timeout} seconds.\n{exc.stdout or ''}\n{exc.stderr or ''}".strip()
            status = "failed"
            returncode = 124
        except OSError as exc:
            combined = str(exc)
            status = "failed"
            returncode = 1
        with self._lock:
            operation = self._operations[operation_id]
            operation["status"] = status
            operation["returncode"] = returncode
            operation["output"] = combined[-100_000:]
            operation["finished_at"] = _timestamp()


def operator_token() -> str:
    configured = os.getenv("DARUL_OPERATOR_TOKEN", "").strip()
    if configured:
        return configured
    token_file = os.getenv("DARUL_OPERATOR_TOKEN_FILE", "").strip()
    if not token_file:
        return ""
    try:
        return Path(token_file).expanduser().read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise GraphEngineError(f"Unable to read DARUL_OPERATOR_TOKEN_FILE: {exc}") from exc


def operator_origins() -> frozenset[str]:
    configured = os.getenv("DARUL_OPERATOR_ORIGINS", "")
    values = {item.strip().rstrip("/") for item in configured.split(",") if item.strip()}
    if not values:
        values = {
            "http://127.0.0.1:4173",
            "http://localhost:4173",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        }
    return frozenset(values)


def create_operation_manager(settings: Settings = SETTINGS) -> OperationManager:
    try:
        workers = int(os.getenv("DARUL_OPERATOR_WORKERS", "1"))
        timeout = int(os.getenv("DARUL_OPERATOR_TIMEOUT", "900"))
    except ValueError as exc:
        raise GraphEngineError("DARUL operator worker and timeout values must be integers") from exc
    return OperationManager(
        RepositoryRegistry.from_settings(settings),
        engine_root=settings.repo_root,
        max_workers=workers,
        timeout=max(1, timeout),
    )
