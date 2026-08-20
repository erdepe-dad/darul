"""Read-only Model Context Protocol server for Darul graph retrieval."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import anyio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool, ToolAnnotations

from .config import SETTINGS, Settings
from .db import GraphDB, GraphEngineError
from .hooks.context_inject import retrieve_context
from .stitcher import inspect_page
from .tracer import trace_view


SERVER_INSTRUCTIONS = """Use Darul before broad filesystem search or repeated source reads. Start with darul_context for the user request, use darul_search to locate graph entities, then darul_trace or darul_inspect_page for bounded structural evidence. Read source only for files identified by Darul, details absent from the graph, stale or unresolved evidence, or implementation and verification. Never claim suggested routes are validated runtime topology."""

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

REPOSITORIES_QUERY = """
MATCH (r:Repository)
OPTIONAL MATCH (r)-[:CONTAINS]->(f:CodeFile)
RETURN r.name AS name, r.root_path AS root_path, r.updated_at AS updated_at,
       count(DISTINCT f) AS files
ORDER BY toLower(r.name)
"""

SEARCH_QUERY = """
MATCH (n)
WHERE n.repo_name = $repo_name
  AND any(label IN labels(n) WHERE label IN $labels)
  AND any(value IN [n.id, n.name, n.path, n.route_path, n.url, n.normalized_url,
                    n.process_key, n.channel, n.system]
          WHERE toLower(coalesce(toString(value), '')) CONTAINS $term)
RETURN n.id AS id, labels(n) AS labels, n.name AS name, n.path AS path,
       n.route_path AS route_path, n.method AS method, n.url AS url,
       n.source_file_id AS source_file_id, n.line AS line
ORDER BY labels(n)[0], toLower(coalesce(n.name, n.path, n.route_path, n.url, n.id))
LIMIT $limit
"""

SEARCH_LABELS = [
    "CodeFile",
    "Class",
    "Function",
    "Page",
    "APIEndpoint",
    "BackendRoute",
    "UIAction",
    "WorkflowProcess",
    "WorkflowStep",
    "ExternalSystem",
    "MessageChannel",
]

server = Server("darul", version="0.1.0", instructions=SERVER_INSTRUCTIONS)


def _settings(repository: str | None) -> Settings:
    repo_name = (repository or SETTINGS.repo_name).strip()
    if not repo_name:
        repo_name = SETTINGS.repo_name
    return replace(SETTINGS, repo_name=repo_name)


def _json_default(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _graph_error(exc: GraphEngineError) -> str:
    return f"Darul graph unavailable: {exc}"


def list_repositories() -> str:
    try:
        with GraphDB() as db:
            rows = db.execute_read(REPOSITORIES_QUERY)
    except GraphEngineError as exc:
        return _graph_error(exc)
    if not rows:
        return "No repositories are currently ingested in Darul."
    lines = ["# Ingested Darul repositories"]
    for row in rows:
        location = f" - {row['root_path']}" if row.get("root_path") else ""
        lines.append(f"- {row['name']} ({row.get('files', 0)} files){location}")
    return "\n".join(lines)


def context_for_prompt(prompt: str, repository: str | None = None) -> str:
    settings = _settings(repository)
    try:
        with GraphDB(settings) as db:
            return retrieve_context(prompt, db, settings)
    except GraphEngineError as exc:
        return _graph_error(exc)


def search_graph(query: str, repository: str | None = None, limit: int = 20) -> str:
    term = query.strip().lower()
    if not term:
        return "Provide a non-empty graph search query."
    bounded_limit = max(1, min(limit, 50))
    settings = _settings(repository)
    try:
        with GraphDB(settings) as db:
            rows = db.execute_read(
                SEARCH_QUERY,
                repo_name=settings.repo_name,
                labels=SEARCH_LABELS,
                term=term,
                limit=bounded_limit,
            )
    except GraphEngineError as exc:
        return _graph_error(exc)
    if not rows:
        return f"No Darul entities matched {query!r} in repository {settings.repo_name!r}."
    lines = [f"# Darul matches in {settings.repo_name}"]
    for row in rows:
        kind = "/".join(row.get("labels") or ["Entity"])
        identity = row.get("name") or row.get("path") or row.get("route_path") or row.get("url") or row.get("id")
        location = row.get("source_file_id") or row.get("path")
        if location and row.get("line"):
            location = f"{location}:{row['line']}"
        detail = f" - {location}" if location else ""
        method = f" [{row['method']}]" if row.get("method") else ""
        lines.append(f"- {kind}: {identity}{method}{detail}")
    return "\n".join(lines)


def inspect_graph_page(page: str, repository: str | None = None) -> str:
    settings = _settings(repository)
    try:
        with GraphDB(settings) as db:
            result = inspect_page(db, page, settings)
    except GraphEngineError as exc:
        return _graph_error(exc)
    return json.dumps(result, indent=2, default=_json_default)


def trace_graph_view(
    view: str,
    repository: str | None = None,
    path_limit: int = 1200,
) -> str:
    settings = _settings(repository)
    bounded_limit = max(100, min(path_limit, 3000))
    try:
        with GraphDB(settings) as db:
            result = trace_view(db, view, settings, path_limit=bounded_limit)
    except GraphEngineError as exc:
        return _graph_error(exc)
    if not result.get("found"):
        warning = "; ".join(result.get("warnings") or [])
        return f"No Darul trace found for {view!r} in {settings.repo_name!r}. {warning}".strip()
    lines = [
        f"# Darul trace: {view}",
        f"Repository: {settings.repo_name}",
    ]
    if result.get("warnings"):
        lines.append("Warnings: " + "; ".join(result["warnings"]))
    lines.extend(["", "```mermaid", result.get("mermaid", ""), "```"])
    return "\n".join(lines)


def _object_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


@server.list_tools()
async def list_tools() -> list[Tool]:
    repository = {
        "type": ["string", "null"],
        "description": "Ingested repository name; defaults to the server working directory name.",
        "default": None,
    }
    return [
        Tool(
            name="darul_repositories",
            description="List repositories already ingested into the shared Darul graph.",
            inputSchema=_object_schema({}),
            annotations=READ_ONLY,
        ),
        Tool(
            name="darul_context",
            description="Retrieve active decisions and prompt-relevant hotspots before reading source.",
            inputSchema=_object_schema(
                {
                    "prompt": {"type": "string", "description": "The user's current request."},
                    "repository": repository,
                },
                ["prompt"],
            ),
            annotations=READ_ONLY,
        ),
        Tool(
            name="darul_search",
            description="Search ingested files, symbols, routes, workflows, systems, and channels.",
            inputSchema=_object_schema(
                {
                    "query": {"type": "string", "description": "Name, path, route, URL, or system term."},
                    "repository": repository,
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                },
                ["query"],
            ),
            annotations=READ_ONLY,
        ),
        Tool(
            name="darul_inspect_page",
            description="Inspect a page/component and its requests and candidate backend handlers.",
            inputSchema=_object_schema(
                {
                    "page": {"type": "string", "description": "Repository-relative page or component path."},
                    "repository": repository,
                },
                ["page"],
            ),
            annotations=READ_ONLY,
        ),
        Tool(
            name="darul_trace",
            description="Trace a view, controller, class, or file through services and workflows.",
            inputSchema=_object_schema(
                {
                    "view": {"type": "string", "description": "Route, class name, or source path."},
                    "repository": repository,
                    "path_limit": {
                        "type": "integer",
                        "minimum": 100,
                        "maximum": 3000,
                        "default": 1200,
                    },
                },
                ["view"],
            ),
            annotations=READ_ONLY,
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    repository = arguments.get("repository")
    handlers = {
        "darul_repositories": lambda: list_repositories(),
        "darul_context": lambda: context_for_prompt(str(arguments["prompt"]), repository),
        "darul_search": lambda: search_graph(
            str(arguments["query"]), repository, int(arguments.get("limit", 20))
        ),
        "darul_inspect_page": lambda: inspect_graph_page(str(arguments["page"]), repository),
        "darul_trace": lambda: trace_graph_view(
            str(arguments["view"]), repository, int(arguments.get("path_limit", 1200))
        ),
    }
    if name not in handlers:
        raise ValueError(f"Unknown Darul tool: {name}")
    return [TextContent(type="text", text=handlers[name]())]


async def run_server() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    anyio.run(run_server)


if __name__ == "__main__":
    main()
