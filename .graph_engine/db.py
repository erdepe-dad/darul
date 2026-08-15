"""Small Bolt-driver abstraction for Neo4j and compatible databases."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import AbstractContextManager
from typing import Any

from .config import SETTINGS, Settings


SCHEMA_QUERIES = (
    "CREATE CONSTRAINT repository_name IF NOT EXISTS FOR (r:Repository) REQUIRE r.name IS UNIQUE",
    "CREATE CONSTRAINT code_file_id IF NOT EXISTS FOR (f:CodeFile) REQUIRE f.id IS UNIQUE",
    "CREATE CONSTRAINT function_id IF NOT EXISTS FOR (fn:Function) REQUIRE fn.id IS UNIQUE",
    "CREATE CONSTRAINT call_site_id IF NOT EXISTS FOR (c:CallSite) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT class_id IF NOT EXISTS FOR (c:Class) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT page_id IF NOT EXISTS FOR (p:Page) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT api_endpoint_id IF NOT EXISTS FOR (a:APIEndpoint) REQUIRE a.id IS UNIQUE",
    "CREATE CONSTRAINT backend_route_id IF NOT EXISTS FOR (b:BackendRoute) REQUIRE b.id IS UNIQUE",
    "CREATE CONSTRAINT workflow_process_id IF NOT EXISTS FOR (p:WorkflowProcess) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT workflow_step_id IF NOT EXISTS FOR (s:WorkflowStep) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT workflow_start_id IF NOT EXISTS FOR (s:WorkflowStart) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT ui_action_id IF NOT EXISTS FOR (a:UIAction) REQUIRE a.id IS UNIQUE",
    "CREATE CONSTRAINT external_system_id IF NOT EXISTS FOR (s:ExternalSystem) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT message_channel_id IF NOT EXISTS FOR (m:MessageChannel) REQUIRE m.id IS UNIQUE",
    "CREATE CONSTRAINT decision_id IF NOT EXISTS FOR (d:Decision) REQUIRE d.id IS UNIQUE",
    "CREATE CONSTRAINT session_id IF NOT EXISTS FOR (s:Session) REQUIRE s.id IS UNIQUE",
    "CREATE INDEX api_endpoint_lookup IF NOT EXISTS FOR (a:APIEndpoint) ON (a.normalized_url, a.method)",
    "CREATE INDEX backend_route_lookup IF NOT EXISTS FOR (b:BackendRoute) ON (b.normalized_url, b.method)",
    "CREATE INDEX class_name_lookup IF NOT EXISTS FOR (c:Class) ON (c.name)",
    "CREATE INDEX class_qualified_name_lookup IF NOT EXISTS FOR (c:Class) ON (c.qualified_name)",
    "CREATE INDEX message_channel_lookup IF NOT EXISTS FOR (m:MessageChannel) ON (m.broker, m.channel)",
)


class GraphEngineError(RuntimeError):
    """Base exception for actionable graph-engine failures."""


class DriverUnavailableError(GraphEngineError):
    pass


class DatabaseConnectionError(GraphEngineError):
    pass


class GraphDB(AbstractContextManager["GraphDB"]):
    def __init__(self, settings: Settings = SETTINGS) -> None:
        self.settings = settings
        self._driver: Any = None

    def connect(self) -> "GraphDB":
        if self._driver is not None:
            return self
        if not self.settings.password:
            raise DatabaseConnectionError(
                "GRAPH_DB_PASS is not configured. Copy .graph_engine/neo4j.env.example "
                "to .graph_engine/neo4j.env or run scripts/deploy-neo4j.sh."
            )
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise DriverUnavailableError(
                "The Neo4j driver is not installed. Run: pip install neo4j>=5.20"
            ) from exc
        try:
            self._driver = GraphDatabase.driver(
                self.settings.uri,
                auth=self.settings.auth,
                connection_timeout=self.settings.connection_timeout,
            )
            self._driver.verify_connectivity()
        except Exception as exc:
            self.close()
            raise DatabaseConnectionError(
                f"Unable to connect to graph database at {self.settings.uri}: {exc}"
            ) from exc
        return self

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def __exit__(self, *args: object) -> None:
        self.close()

    def _session(self) -> Any:
        self.connect()
        kwargs = {"database": self.settings.database} if self.settings.database else {}
        return self._driver.session(**kwargs)

    def execute_write(self, query: str, **parameters: Any) -> list[dict[str, Any]]:
        with self._session() as session:
            return session.execute_write(
                lambda tx: [record.data() for record in tx.run(query, **parameters)]
            )

    def execute_read(self, query: str, **parameters: Any) -> list[dict[str, Any]]:
        with self._session() as session:
            return session.execute_read(
                lambda tx: [record.data() for record in tx.run(query, **parameters)]
            )

    def execute_many(
        self, query: str, rows: Iterable[Mapping[str, Any]], *, batch_size: int = 500
    ) -> int:
        batch: list[Mapping[str, Any]] = []
        total = 0
        for row in rows:
            batch.append(row)
            if len(batch) >= batch_size:
                self.execute_write(query, rows=batch)
                total += len(batch)
                batch = []
        if batch:
            self.execute_write(query, rows=batch)
            total += len(batch)
        return total

    def ensure_schema(self) -> None:
        for query in SCHEMA_QUERIES:
            self.execute_write(query)

    def healthcheck(self) -> dict[str, Any]:
        rows = self.execute_read(
            "RETURN 1 AS ok, $repo_name AS repo_name", repo_name=self.settings.repo_name
        )
        return rows[0] if rows else {"ok": 0, "repo_name": self.settings.repo_name}
