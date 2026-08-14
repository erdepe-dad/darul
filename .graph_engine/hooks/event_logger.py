"""Record Claude Code session activity and architectural decisions."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import SETTINGS, Settings
from ..db import GraphDB, GraphEngineError


def _read_payload() -> dict[str, Any]:
    if sys.stdin.isatty():
        return {}
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}


def _session_id(payload: dict[str, Any], settings: Settings) -> str:
    raw = payload.get("session_id") or payload.get("sessionId") or "standalone"
    return f"{settings.repo_name}:{raw}"


def _find_file_paths(value: Any, settings: Settings) -> list[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"file_path", "filepath", "path", "file"} and isinstance(item, str):
                candidate = Path(item)
                try:
                    found.add(candidate.resolve().relative_to(settings.repo_root).as_posix())
                except (OSError, ValueError):
                    if not candidate.is_absolute():
                        found.add(candidate.as_posix().removeprefix("./"))
            else:
                found.update(_find_file_paths(item, settings))
    elif isinstance(value, list):
        for item in value:
            found.update(_find_file_paths(item, settings))
    return sorted(found)


LOG_EVENT_QUERY = """
MERGE (r:Repository {name: $repo_name})
MERGE (s:Session {id: $session_id})
ON CREATE SET s.timestamp = datetime()
SET s.last_activity = datetime()
MERGE (r)-[:HAS_SESSION]->(s)
CREATE (e:SessionEvent {id: $event_id, event_type: $event_type, payload: $payload,
                        timestamp: datetime(), repo_name: $repo_name})
CREATE (s)-[:RECORDED]->(e)
WITH e
UNWIND $file_ids AS file_id
OPTIONAL MATCH (f:CodeFile {id: file_id})
FOREACH (_ IN CASE WHEN f IS NULL THEN [] ELSE [1] END | MERGE (e)-[:TOUCHED]->(f))
"""


def log_event(db: GraphDB, payload: dict[str, Any], settings: Settings = SETTINGS) -> str:
    event_id = f"{settings.repo_name}:{uuid.uuid4()}"
    event_type = str(payload.get("hook_event_name") or payload.get("event") or "unknown")
    paths = _find_file_paths(payload, settings)
    db.execute_write(
        LOG_EVENT_QUERY,
        repo_name=settings.repo_name,
        session_id=_session_id(payload, settings),
        event_id=event_id,
        event_type=event_type,
        payload=json.dumps(payload, separators=(",", ":"), default=str)[:100_000],
        file_ids=[f"{settings.repo_name}:{path}" for path in paths],
    )
    return event_id


CREATE_DECISION_QUERY = """
MERGE (r:Repository {name: $repo_name})
MERGE (s:Session {id: $session_id})
ON CREATE SET s.timestamp = datetime()
MERGE (r)-[:HAS_SESSION]->(s)
CREATE (d:Decision {id: $decision_id, title: $title, rationale: $rationale,
                    status: 'ACTIVE', date: datetime(), repo_name: $repo_name})
CREATE (s)-[:MADE_DECISION]->(d)
WITH d
UNWIND $file_ids AS file_id
OPTIONAL MATCH (f:CodeFile {id: file_id})
FOREACH (_ IN CASE WHEN f IS NULL THEN [] ELSE [1] END | MERGE (d)-[:AFFECTS]->(f))
"""


SUPERSEDE_DECISION_QUERY = """
MATCH (old:Decision {id: $old_decision_id})
SET old.status = 'SUPERSEDED'
WITH old
MERGE (r:Repository {name: $repo_name})
MERGE (s:Session {id: $session_id})
ON CREATE SET s.timestamp = datetime()
MERGE (r)-[:HAS_SESSION]->(s)
CREATE (d:Decision {id: $decision_id, title: $title, rationale: $rationale,
                    status: 'ACTIVE', date: datetime(), repo_name: $repo_name})
CREATE (s)-[:MADE_DECISION]->(d)
CREATE (d)-[:SUPERSEDES]->(old)
WITH d
UNWIND $file_ids AS file_id
OPTIONAL MATCH (f:CodeFile {id: file_id})
FOREACH (_ IN CASE WHEN f IS NULL THEN [] ELSE [1] END | MERGE (d)-[:AFFECTS]->(f))
"""


def record_decision(
    db: GraphDB,
    title: str,
    rationale: str,
    files: list[str],
    *,
    session: str = "standalone",
    supersedes: str | None = None,
    settings: Settings = SETTINGS,
) -> str:
    decision_id = f"{settings.repo_name}:{uuid.uuid4()}"
    parameters = {
        "repo_name": settings.repo_name,
        "session_id": f"{settings.repo_name}:{session}",
        "decision_id": decision_id,
        "title": title,
        "rationale": rationale,
        "file_ids": [f"{settings.repo_name}:{item.removeprefix('./')}" for item in files],
    }
    if supersedes:
        parameters["old_decision_id"] = supersedes
        db.execute_write(SUPERSEDE_DECISION_QUERY, **parameters)
    else:
        db.execute_write(CREATE_DECISION_QUERY, **parameters)
    return decision_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision", action="store_true", help="Record a decision instead of a hook event")
    parser.add_argument("--title")
    parser.add_argument("--rationale")
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--session", default="standalone")
    parser.add_argument("--supersedes")
    args = parser.parse_args(argv)
    try:
        with GraphDB() as db:
            if args.decision:
                if not args.title or not args.rationale:
                    parser.error("--decision requires --title and --rationale")
                print(record_decision(db, args.title, args.rationale, args.file, session=args.session, supersedes=args.supersedes))
            else:
                print(log_event(db, _read_payload()))
        return 0
    except GraphEngineError as exc:
        print(f"graph event logger: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
