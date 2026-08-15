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
    value = re.sub(r"^/+(?=[A-Za-z][A-Za-z0-9+.-]*://)", "", value)
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
MATCH (a:APIEndpoint)
MATCH (b:BackendRoute {normalized_url: a.normalized_url, method: a.method})
MERGE (a)-[st:TARGETS_ROUTE]->(b)
ON CREATE SET st.created_at = datetime()
SET st.managed_by = 'stitcher', st.resolution = 'exact'
RETURN count(st) AS stitched
"""


MAPPED_REQUEST_QUERY = """
MATCH (a:APIEndpoint)-[:TARGETS_SYSTEM]->(s:ExternalSystem)
WHERE coalesce(s.path_prefix, '') <> ''
RETURN a.id AS request_id, a.method AS method,
       a.normalized_url AS normalized_url, s.name AS system,
       s.path_prefix AS path_prefix, coalesce(s.target_repo, '') AS target_repo
"""


STITCH_MAPPED_QUERY = """
UNWIND $rows AS row
MATCH (a:APIEndpoint {id: row.request_id})
MATCH (b:BackendRoute {normalized_url: row.normalized_url, method: row.method})
WHERE row.target_repo = '' OR b.repo_name = row.target_repo
MERGE (a)-[st:TARGETS_ROUTE]->(b)
ON CREATE SET st.created_at = datetime()
SET st.managed_by = 'stitcher', st.resolution = 'configured-prefix',
    st.system = row.system, st.path_prefix = row.path_prefix
RETURN count(st) AS stitched
"""


CLEAR_MAPPED_STITCHES_QUERY = """
MATCH (:APIEndpoint)-[st:TARGETS_ROUTE]->(:BackendRoute)
WHERE st.managed_by = 'stitcher' AND st.resolution = 'configured-prefix'
DELETE st
"""


CONFIGURE_SERVICE_QUERY = """
MERGE (s:ExternalSystem {id: $system_id})
SET s.name = $key, s.repo_name = $repo_name, s.base_url = $base_url,
    s.path_prefix = $path_prefix, s.target_repo = $target_repo,
    s.configured_at = datetime()
RETURN properties(s) AS service
"""


LIST_SERVICES_QUERY = """
MATCH (s:ExternalSystem)
WHERE ($repo_name = '' OR s.repo_name = $repo_name)
RETURN s.id AS id, s.repo_name AS repo_name, s.name AS key,
       s.base_url AS base_url, s.path_prefix AS path_prefix,
       s.target_repo AS target_repo
ORDER BY toLower(s.repo_name), toLower(s.name)
"""


CLEAR_SERVICE_QUERY = """
MATCH (s:ExternalSystem {id: $system_id})
SET s.base_url = '', s.path_prefix = '', s.target_repo = ''
REMOVE s.configured_at
RETURN properties(s) AS service
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
    # Revisit every request so cross-repository links do not depend on build order.
    _ = settings
    rows = db.execute_write(STITCH_QUERY)
    stitched = int(rows[0]["stitched"]) if rows else 0
    db.execute_write(CLEAR_MAPPED_STITCHES_QUERY)
    mapped_requests = db.execute_read(MAPPED_REQUEST_QUERY)
    mapped_rows = [
        {
            "request_id": row["request_id"],
            "method": row["method"],
            "normalized_url": normalize_url(
                f"{row['path_prefix'].rstrip('/')}/{row['normalized_url'].lstrip('/')}"
            ),
            "system": row["system"],
            "path_prefix": normalize_url(row["path_prefix"]),
            "target_repo": row["target_repo"],
        }
        for row in mapped_requests
        if {
            "request_id", "method", "normalized_url", "system",
            "path_prefix", "target_repo",
        }.issubset(row)
    ]
    if mapped_rows:
        mapped = db.execute_write(STITCH_MAPPED_QUERY, rows=mapped_rows)
        stitched += int(mapped[0]["stitched"]) if mapped else 0
    return stitched


def service_path_prefix(base_url: str) -> str:
    value = base_url.strip()
    path = urlsplit(value).path if "://" in value else value
    normalized = normalize_url(path)
    return "" if normalized == "/" else normalized


def configure_service(
    db: GraphDB,
    key: str,
    base_url: str,
    *,
    repo_name: str,
    target_repo: str = "",
) -> dict:
    clean_key = key.strip()
    if not clean_key:
        raise ValueError("service key cannot be empty")
    clean_url = base_url.strip()
    if not clean_url:
        raise ValueError("base URL cannot be empty")
    rows = db.execute_write(
        CONFIGURE_SERVICE_QUERY,
        system_id=f"{repo_name}:system:{clean_key}",
        repo_name=repo_name,
        key=clean_key,
        base_url=clean_url,
        path_prefix=service_path_prefix(clean_url),
        target_repo=target_repo.strip(),
    )
    return rows[0]["service"] if rows else {}


def list_services(db: GraphDB, repo_name: str = "") -> list[dict]:
    return db.execute_read(LIST_SERVICES_QUERY, repo_name=repo_name.strip())


def clear_service(db: GraphDB, key: str, *, repo_name: str) -> dict:
    rows = db.execute_write(
        CLEAR_SERVICE_QUERY,
        system_id=f"{repo_name}:system:{key.strip()}",
    )
    return rows[0]["service"] if rows else {}


def inspect_page(db: GraphDB, page: str, settings: Settings = SETTINGS) -> dict:
    normalized_page = page.removeprefix("./")
    rows = db.execute_read(INSPECT_QUERY, repo_name=settings.repo_name, page=normalized_page)
    return rows[0] if rows else {"page": normalized_page, "route_path": None, "requests": []}
