"""Render active graph decisions and structural hotspots for prompt injection."""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from ..config import SETTINGS, Settings
from ..db import GraphDB, GraphEngineError


DECISIONS_QUERY = """
MATCH (r:Repository {name: $repo_name})-[:HAS_SESSION]->(:Session)-[:MADE_DECISION]->(d:Decision)
OPTIONAL MATCH ()-[incoming]->(d)
WHERE type(incoming) = 'SUPERSEDES'
WITH d, incoming
WHERE incoming IS NULL AND coalesce(d.status, 'ACTIVE') = 'ACTIVE'
OPTIONAL MATCH (d)-[:AFFECTS]->(f:CodeFile)
OPTIONAL MATCH (d)-[lineage]->(old:Decision)
WHERE type(lineage) = 'SUPERSEDES'
RETURN d.title AS active_decision, d.rationale AS rationale,
       collect(DISTINCT f.path) AS affected_files, old.title AS superseded_decision,
       d.date AS date
ORDER BY date DESC LIMIT 5
"""


HOTSPOTS_QUERY = """
MATCH (r:Repository {name: $repo_name})-[:CONTAINS]->(f:CodeFile)
WHERE any(term IN $terms WHERE toLower(f.path) CONTAINS term)
OPTIONAL MATCH (f)-[:DEFINES]->(symbol)
OPTIONAL MATCH (f)-[:CONTAINS]->(:Page)-[:MAKES_REQUEST]->(a:APIEndpoint)-[route:TARGETS_ROUTE]->(b:BackendRoute)
WHERE route.trust_status = 'VALIDATED'
RETURN f.path AS path, collect(DISTINCT symbol.name)[0..8] AS symbols,
       collect(DISTINCT a.method + ' ' + a.normalized_url + ' -> ' + coalesce(b.repo_name, '?') + ':' + coalesce(b.route_path, '?'))[0..8] AS request_routes
LIMIT 8
"""


def _prompt_from_stdin() -> str:
    if sys.stdin.isatty():
        return ""
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
        return str(payload.get("prompt") or payload.get("user_prompt") or payload.get("message") or "")
    except json.JSONDecodeError:
        return raw


def _terms(prompt: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9_.\-/]{3,}", prompt.lower())
    ignored = {"the", "and", "for", "with", "this", "that", "from", "into", "please"}
    return list(dict.fromkeys(word for word in words if word not in ignored))[:12]


def render_context(decisions: list[dict[str, Any]], hotspots: list[dict[str, Any]], unavailable: str | None = None) -> str:
    lines = ["--- GRAPH KNOWLEDGE CONTEXT ---", "[Active Architecture & Decisions]"]
    if decisions:
        for row in decisions:
            files = ", ".join(item for item in row.get("affected_files", []) if item) or "none recorded"
            lines.append(f"- Title: {row.get('active_decision')} (Files: {files})")
            lines.append(f"  Rationale: {row.get('rationale')}")
            if row.get("superseded_decision"):
                lines.append(f"  Supersedes: {row['superseded_decision']}")
    else:
        lines.append("- No active decisions recorded.")
    if hotspots:
        lines.append("[Prompt-Relevant Structural Hotspots]")
        for row in hotspots:
            symbols = ", ".join(item for item in row.get("symbols", []) if item)
            lines.append(f"- {row.get('path')}" + (f" (Symbols: {symbols})" if symbols else ""))
            for route in (item for item in row.get("request_routes", []) if item):
                lines.append(f"  API: {route}")
    if unavailable:
        lines.append(f"[Graph unavailable: {unavailable}]")
    lines.append("--------------------------------")
    return "\n".join(lines)


def retrieve_context(prompt: str, db: GraphDB, settings: Settings = SETTINGS) -> str:
    decisions = db.execute_read(DECISIONS_QUERY, repo_name=settings.repo_name)
    terms = _terms(prompt)
    hotspots = db.execute_read(HOTSPOTS_QUERY, repo_name=settings.repo_name, terms=terms) if terms else []
    return render_context(decisions, hotspots)


def main() -> int:
    prompt = _prompt_from_stdin()
    try:
        with GraphDB() as db:
            print(retrieve_context(prompt, db))
    except GraphEngineError as exc:
        print(render_context([], [], str(exc)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
