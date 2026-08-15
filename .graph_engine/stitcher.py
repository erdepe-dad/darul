"""Normalize and connect frontend API calls to backend routes."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from .config import SETTINGS, Settings
from .db import GraphDB


PARAMETER_PATTERNS = (
    re.compile(r"\$\{[^}/]+\}"),
    re.compile(r"(?<=/):[^/]+"),
    re.compile(r"\{[^}/]+\}"),
    re.compile(r"\[[^]/]+\]"),
)


def normalize_url(url: str) -> str:
    """Convert route parameter dialects into a comparable path."""
    value = url.strip().strip("`'\"")
    if "://" in value:
        value = urlsplit(value).path
    else:
        value = value.split("?", 1)[0].split("#", 1)[0]
    value = re.sub(r"^\$?[A-Z_][A-Z0-9_]*", "", value)
    if not value.startswith("/"):
        value = "/" + value
    for pattern in PARAMETER_PATTERNS:
        value = pattern.sub("{param}", value)
    value = re.sub(r"/{2,}", "/", value)
    if len(value) > 1:
        value = value.rstrip("/")
    return value or "/"


STITCH_QUERY = """
MATCH (a:APIEndpoint {repo_name: $repo_name})
MATCH (b:BackendRoute {normalized_url: a.normalized_url, method: a.method})
MERGE (a)-[st:TARGETS_ROUTE]->(b)
ON CREATE SET st.created_at = datetime()
RETURN count(st) AS stitched
"""


INSPECT_QUERY = """
MATCH (r:Repository {name: $repo_name})-[:CONTAINS]->(f:CodeFile {path: $page})
OPTIONAL MATCH (f)-[:CONTAINS]->(p:Page)-[:MAKES_REQUEST]->(a:APIEndpoint)
OPTIONAL MATCH (a)-[:TARGETS_ROUTE]->(b:BackendRoute)-[:HANDLED_BY]->(fn:Function)
RETURN f.path AS page, p.route_path AS route_path, f.frameworks AS frameworks,
       f.workflow_refs AS workflow_refs,
       collect(DISTINCT {method: a.method, url: a.url, normalized_url: a.normalized_url,
                         backend_repo: b.repo_name, backend_route: b.route_path,
                         handler: fn.name, handler_file: fn.source_file_id}) AS requests
"""


def stitch_endpoints(db: GraphDB, settings: Settings = SETTINGS) -> int:
    rows = db.execute_write(STITCH_QUERY, repo_name=settings.repo_name)
    return int(rows[0]["stitched"]) if rows else 0


def inspect_page(db: GraphDB, page: str, settings: Settings = SETTINGS) -> dict:
    normalized_page = page.removeprefix("./")
    rows = db.execute_read(INSPECT_QUERY, repo_name=settings.repo_name, page=normalized_page)
    return rows[0] if rows else {"page": normalized_page, "route_path": None, "requests": []}
