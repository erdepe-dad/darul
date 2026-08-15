"""Read-only HTTP API and static server for the graph visualization."""

from __future__ import annotations

import json
import hmac
import mimetypes
import threading
import webbrowser
from dataclasses import dataclass, replace
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .config import SETTINGS, Settings
from .db import GraphDB, GraphEngineError
from .operator_api import (
    OperationManager,
    OperatorService,
    create_operation_manager,
    operator_origins,
    operator_token,
)
from .tracer import trace_view


WEB_ROOT = Path(__file__).resolve().parent / "web"
DEFAULT_LABELS = (
    "Repository",
    "CodeFile",
    "Class",
    "Function",
    "Page",
    "APIEndpoint",
    "BackendRoute",
    "WorkflowProcess",
    "WorkflowStep",
    "UIAction",
    "ExternalSystem",
    "MessageChannel",
    "Decision",
    "Session",
    "SessionEvent",
)


META_REPOSITORIES_QUERY = """
MATCH (n)
WHERE n.repo_name IS NOT NULL OR n:Repository OR n.id IS NOT NULL
WITH coalesce(n.repo_name,
              CASE WHEN n:Repository THEN n.name ELSE split(toString(n.id), ':')[0] END) AS repo
WHERE repo IS NOT NULL AND repo <> ''
RETURN repo AS name, count(*) AS nodes
ORDER BY toLower(repo)
"""

META_LABELS_QUERY = """
MATCH (n)
WHERE n.repo_name IS NOT NULL OR n:Repository OR n.id IS NOT NULL
UNWIND labels(n) AS label
RETURN label, count(*) AS count
ORDER BY count DESC, label
"""

META_RELATIONSHIPS_QUERY = """
MATCH ()-[r]->()
RETURN type(r) AS relationship, count(*) AS count
ORDER BY count DESC, relationship
"""

NODE_QUERY = """
MATCH (n)
WHERE n.repo_name IS NOT NULL OR n:Repository OR n.id IS NOT NULL
WITH n, coalesce(n.repo_name,
                 CASE WHEN n:Repository THEN n.name ELSE split(toString(n.id), ':')[0] END) AS repository,
     toLower(coalesce(toString(n.name), toString(n.path), toString(n.title),
                      toString(n.route_path), toString(n.url), toString(n.id), '')) AS searchable
WHERE ($repository = '' OR repository = $repository)
  AND (size($labels) = 0 OR any(label IN labels(n) WHERE label IN $labels))
  AND ($search = '' OR searchable CONTAINS $search)
RETURN elementId(n) AS element_id, labels(n) AS labels, properties(n) AS properties,
       repository
LIMIT $limit
"""

FOCUS_QUERY_TEMPLATE = """
MATCH (center)
WHERE elementId(center) = $focus OR toString(center.id) = $focus
MATCH (center)-[*0..%d]-(n)
WITH DISTINCT n,
     coalesce(n.repo_name,
              CASE WHEN n:Repository THEN n.name ELSE split(toString(n.id), ':')[0] END) AS repository,
     toLower(coalesce(toString(n.name), toString(n.path), toString(n.title),
                      toString(n.route_path), toString(n.url), toString(n.id), '')) AS searchable
WHERE ($repository = '' OR repository = $repository)
  AND (size($labels) = 0 OR any(label IN labels(n) WHERE label IN $labels))
  AND ($search = '' OR searchable CONTAINS $search)
RETURN elementId(n) AS element_id, labels(n) AS labels, properties(n) AS properties,
       repository
LIMIT $limit
"""

EDGE_QUERY = """
MATCH (source)-[r]->(target)
WHERE elementId(source) IN $element_ids AND elementId(target) IN $element_ids
  AND (size($relationship_types) = 0 OR type(r) IN $relationship_types)
RETURN elementId(r) AS element_id, elementId(source) AS source,
       elementId(target) AS target, type(r) AS relationship, properties(r) AS properties
LIMIT $edge_limit
"""

DETAIL_QUERY = """
MATCH (n)
WHERE elementId(n) = $node_id OR toString(n.id) = $node_id
OPTIONAL MATCH (n)-[r]-(neighbor)
WITH n, r, neighbor
ORDER BY type(r), coalesce(neighbor.name, neighbor.path, neighbor.title, neighbor.id)
RETURN elementId(n) AS element_id, labels(n) AS labels, properties(n) AS properties,
       collect(CASE WHEN r IS NULL THEN NULL ELSE {
         relationship: type(r), direction: CASE WHEN startNode(r) = n THEN 'out' ELSE 'in' END,
         node_id: elementId(neighbor), labels: labels(neighbor),
         label: coalesce(neighbor.name, neighbor.path, neighbor.title,
                         neighbor.route_path, neighbor.url, neighbor.id, elementId(neighbor))
       } END)[0..100] AS neighbors
"""


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "iso_format"):
        return value.iso_format()
    if hasattr(value, "to_native"):
        return value.to_native()
    return str(value)


def _node_label(row: dict[str, Any]) -> str:
    properties = row.get("properties") or {}
    return str(
        properties.get("name")
        or properties.get("path")
        or properties.get("title")
        or properties.get("route_path")
        or properties.get("url")
        or properties.get("id")
        or row["element_id"]
    )


class GraphVisualizationService:
    def __init__(self, db: GraphDB, settings: Settings = SETTINGS) -> None:
        self.db = db
        self.settings = settings

    def metadata(self) -> dict[str, Any]:
        repositories = self.db.execute_read(META_REPOSITORIES_QUERY)
        labels = self.db.execute_read(META_LABELS_QUERY)
        relationships = self.db.execute_read(META_RELATIONSHIPS_QUERY)
        return {
            "repositories": repositories,
            "labels": labels,
            "relationships": relationships,
            "default_repository": self.settings.repo_name,
            "default_labels": DEFAULT_LABELS,
        }

    def graph(
        self,
        *,
        repository: str = "",
        labels: list[str] | None = None,
        relationship_types: list[str] | None = None,
        search: str = "",
        focus: str = "",
        depth: int = 2,
        limit: int = 240,
    ) -> dict[str, Any]:
        selected_labels = labels or []
        selected_relationships = relationship_types or []
        parameters = {
            "repository": repository,
            "labels": selected_labels,
            "search": search.strip().lower(),
            "limit": max(20, min(limit, 600)),
        }
        if focus:
            parameters["focus"] = focus
            query = FOCUS_QUERY_TEMPLATE % (1 if depth <= 1 else 2)
        else:
            query = NODE_QUERY
        rows = self.db.execute_read(query, **parameters)
        element_ids = [row["element_id"] for row in rows]
        edge_limit = min(max(len(element_ids) * 8, 100), 4000)
        edges = (
            self.db.execute_read(
                EDGE_QUERY,
                element_ids=element_ids,
                relationship_types=selected_relationships,
                edge_limit=edge_limit,
            )
            if element_ids
            else []
        )
        nodes = [
            {
                "id": row["element_id"],
                "entity_id": (row.get("properties") or {}).get("id"),
                "label": _node_label(row),
                "labels": row.get("labels") or [],
                "repository": row.get("repository"),
                "properties": row.get("properties") or {},
            }
            for row in rows
        ]
        links = [
            {
                "id": row["element_id"],
                "source": row["source"],
                "target": row["target"],
                "type": row["relationship"],
                "properties": row.get("properties") or {},
            }
            for row in edges
        ]
        return {
            "nodes": nodes,
            "links": links,
            "scope": {
                "repository": repository,
                "labels": selected_labels,
                "relationships": selected_relationships,
                "search": search,
                "focus": focus,
                "depth": depth,
                "limit": parameters["limit"],
            },
            "truncated": len(rows) >= parameters["limit"],
        }

    def node_detail(self, node_id: str) -> dict[str, Any] | None:
        rows = self.db.execute_read(DETAIL_QUERY, node_id=node_id)
        if not rows or rows[0].get("element_id") is None:
            return None
        row = rows[0]
        return {
            "id": row["element_id"],
            "labels": row.get("labels") or [],
            "properties": row.get("properties") or {},
            "neighbors": [item for item in (row.get("neighbors") or []) if item],
        }

    def trace(self, view: str, repository: str = "", path_limit: int = 1200) -> dict[str, Any]:
        settings = replace(self.settings, repo_name=repository) if repository else self.settings
        return trace_view(self.db, view, settings, path_limit=path_limit)


@dataclass(slots=True)
class VisualizationApplication:
    service: GraphVisualizationService
    web_root: Path = WEB_ROOT
    operator_service: OperatorService | None = None
    operation_manager: OperationManager | None = None
    allowed_origins: frozenset[str] = frozenset()
    mutation_token: str = ""

    def handler_class(self) -> type[BaseHTTPRequestHandler]:
        application = self

        class VisualizationHandler(BaseHTTPRequestHandler):
            server_version = "DarulGraph/0.1"

            def log_message(self, format: str, *args: object) -> None:
                return

            def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
                body = json.dumps(payload, default=_json_default, separators=(",", ":")).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(body)

            def _send_cors_headers(self) -> None:
                origin = self.headers.get("Origin", "").rstrip("/")
                if origin and origin in application.allowed_origins:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                    self.send_header("Vary", "Origin")

            def _read_json(self) -> dict[str, Any]:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError as exc:
                    raise ValueError("invalid content length") from exc
                if length <= 0 or length > 65_536:
                    raise ValueError("request body must be between 1 and 65536 bytes")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("request body must be a JSON object")
                return payload

            def _operator(self) -> tuple[OperatorService, OperationManager]:
                if application.operator_service is None or application.operation_manager is None:
                    raise GraphEngineError("operator API is unavailable")
                return application.operator_service, application.operation_manager

            def _require_mutation_token(self) -> bool:
                expected = application.mutation_token
                if not expected:
                    self._send_json(
                        {"error": "DARUL_OPERATOR_TOKEN is not configured"},
                        HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                    return False
                supplied = self.headers.get("Authorization", "")
                if not supplied.startswith("Bearer ") or not hmac.compare_digest(supplied[7:], expected):
                    self._send_json({"error": "operator authorization failed"}, HTTPStatus.UNAUTHORIZED)
                    return False
                return True

            def do_OPTIONS(self) -> None:
                origin = self.headers.get("Origin", "").rstrip("/")
                if origin not in application.allowed_origins:
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return
                self.send_response(HTTPStatus.NO_CONTENT)
                self._send_cors_headers()
                self.end_headers()

            def _query_list(self, query: dict[str, list[str]], name: str) -> list[str]:
                return [item for value in query.get(name, []) for item in value.split(",") if item]

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                try:
                    if parsed.path == "/api/health":
                        self._send_json({"ok": True, "database": application.service.db.healthcheck()})
                        return
                    if parsed.path == "/api/operator/overview":
                        operator, manager = self._operator()
                        payload = operator.overview()
                        payload["operations"] = manager.list()
                        payload["mutations_enabled"] = bool(application.mutation_token)
                        self._send_json(payload)
                        return
                    if parsed.path == "/api/operator/unresolved":
                        operator, _ = self._operator()
                        self._send_json(
                            operator.unresolved(
                                query.get("repository", [""])[0],
                                int(query.get("limit", ["100"])[0]),
                            )
                        )
                        return
                    if parsed.path == "/api/operator/operations":
                        _, manager = self._operator()
                        self._send_json({"operations": manager.list()})
                        return
                    if parsed.path == "/api/meta":
                        self._send_json(application.service.metadata())
                        return
                    if parsed.path == "/api/graph":
                        self._send_json(
                            application.service.graph(
                                repository=query.get("repository", [""])[0],
                                labels=self._query_list(query, "labels"),
                                relationship_types=self._query_list(query, "relationships"),
                                search=query.get("search", [""])[0],
                                focus=query.get("focus", [""])[0],
                                depth=int(query.get("depth", ["2"])[0]),
                                limit=int(query.get("limit", ["240"])[0]),
                            )
                        )
                        return
                    if parsed.path == "/api/trace":
                        view = query.get("view", [""])[0].strip()
                        if not view:
                            self._send_json({"error": "The view query parameter is required."}, HTTPStatus.BAD_REQUEST)
                            return
                        self._send_json(
                            application.service.trace(
                                view,
                                repository=query.get("repository", [""])[0],
                                path_limit=int(query.get("path_limit", ["1200"])[0]),
                            )
                        )
                        return
                    if parsed.path.startswith("/api/node/"):
                        node_id = unquote(parsed.path.removeprefix("/api/node/"))
                        detail = application.service.node_detail(node_id)
                        if detail is None:
                            self._send_json({"error": "Node not found"}, HTTPStatus.NOT_FOUND)
                        else:
                            self._send_json(detail)
                        return
                    if parsed.path.startswith("/api/"):
                        self._send_json({"error": "Unknown API endpoint"}, HTTPStatus.NOT_FOUND)
                        return
                    self._serve_static(parsed.path)
                except (GraphEngineError, ValueError) as exc:
                    self._send_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
                except BrokenPipeError:
                    return

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                try:
                    if not parsed.path.startswith("/api/operator/"):
                        self._send_json({"error": "Unknown API endpoint"}, HTTPStatus.NOT_FOUND)
                        return
                    if not self._require_mutation_token():
                        return
                    payload = self._read_json()
                    operator, manager = self._operator()
                    if parsed.path == "/api/operator/services":
                        self._send_json(operator.set_service(payload), HTTPStatus.CREATED)
                        return
                    if parsed.path == "/api/operator/services/clear":
                        self._send_json(operator.clear_service(payload))
                        return
                    if parsed.path == "/api/operator/operations":
                        operation = manager.start(
                            str(payload.get("action") or ""),
                            str(payload.get("repository") or ""),
                        )
                        self._send_json(operation, HTTPStatus.ACCEPTED)
                        return
                    self._send_json({"error": "Unknown API endpoint"}, HTTPStatus.NOT_FOUND)
                except (ValueError, json.JSONDecodeError) as exc:
                    self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                except GraphEngineError as exc:
                    self._send_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
                except BrokenPipeError:
                    return

            def _serve_static(self, request_path: str) -> None:
                relative = "index.html" if request_path in {"", "/"} else unquote(request_path.lstrip("/"))
                candidate = (application.web_root / relative).resolve()
                if application.web_root.resolve() not in candidate.parents and candidate != application.web_root.resolve():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                if not candidate.is_file():
                    candidate = application.web_root / "index.html"
                body = candidate.read_bytes()
                content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'")
                self.end_headers()
                self.wfile.write(body)

        return VisualizationHandler


def serve_visualization(
    *,
    host: str = "127.0.0.1",
    port: int = 38533,
    open_browser: bool = False,
    settings: Settings = SETTINGS,
) -> None:
    db = GraphDB(settings).connect()
    application = VisualizationApplication(
        GraphVisualizationService(db, settings),
        operator_service=OperatorService(db, settings),
        operation_manager=create_operation_manager(settings),
        allowed_origins=operator_origins(),
        mutation_token=operator_token(),
    )
    server = ThreadingHTTPServer((host, port), application.handler_class())
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{display_host}:{server.server_port}/"
    print(f"Darul graph visualization: {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        db.close()
