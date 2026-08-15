"""Incremental Git-diff synchronization."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import SETTINGS, Settings
from .db import GraphDB, GraphEngineError
from .parser import SOURCE_EXTENSIONS, ingest_files, parse_file, reconcile_structural_links
from .stitcher import stitch_endpoints


@dataclass(slots=True)
class Change:
    status: str
    path: str
    old_path: str | None = None


@dataclass(slots=True)
class SyncResult:
    added_or_modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


def git_changes(settings: Settings = SETTINGS, base: str = "ORIG_HEAD", head: str = "HEAD") -> list[Change]:
    try:
        result = subprocess.run(
            ["git", "-C", str(settings.repo_root), "diff", "--name-status", "--find-renames", base, head],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except FileNotFoundError as exc:
        raise GraphEngineError("Git is required for incremental synchronization") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or "the requested Git revisions are unavailable"
        raise GraphEngineError(f"Unable to calculate Git changes: {detail}") from exc
    changes: list[Change] = []
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        status = fields[0][0]
        if status in {"R", "C"} and len(fields) >= 3:
            changes.append(Change(status, fields[2], fields[1]))
        elif len(fields) >= 2:
            changes.append(Change(status, fields[1]))
    return changes


DELETE_FILE_CHILDREN_QUERY = """
MATCH (n {repo_name: $repo_name, source_file_id: $file_id})
WHERE n:Class OR n:Function OR n:Page OR n:APIEndpoint OR n:BackendRoute
   OR n:CallSite OR n:WorkflowProcess OR n:WorkflowStep OR n:WorkflowStart OR n:UIAction
   OR n:ExternalSystem OR n:MessageChannel
DETACH DELETE n
"""

DELETE_DIRECT_CHILDREN_QUERY = """
MATCH (f:CodeFile {id: $file_id})-[:DEFINES|CONTAINS]->(child)
DETACH DELETE child
"""

DELETE_FILE_NODE_QUERY = """
MATCH (f:CodeFile {id: $file_id})
DETACH DELETE f
"""

FILE_CLASS_IDENTIFIERS_QUERY = """
MATCH (f:CodeFile {id: $file_id})-[:DEFINES]->(class:Class)
RETURN class.name AS name, class.qualified_name AS qualified_name, class.aliases AS aliases
"""


def _file_class_identifiers(db: GraphDB, rel_path: str, settings: Settings) -> set[str]:
    rows = db.execute_read(
        FILE_CLASS_IDENTIFIERS_QUERY,
        file_id=f"{settings.repo_name}:{rel_path}",
    )
    identifiers: set[str] = set()
    for row in rows:
        identifiers.update(
            value
            for value in (row.get("name"), row.get("qualified_name"), *(row.get("aliases") or []))
            if value
        )
    return identifiers


def clear_file_children(db: GraphDB, rel_path: str, settings: Settings = SETTINGS) -> None:
    parameters = {
        "repo_name": settings.repo_name,
        "file_id": f"{settings.repo_name}:{rel_path}",
    }
    db.execute_write(
        DELETE_FILE_CHILDREN_QUERY,
        **parameters,
    )
    db.execute_write(DELETE_DIRECT_CHILDREN_QUERY, **parameters)


def delete_file(db: GraphDB, rel_path: str, settings: Settings = SETTINGS) -> None:
    clear_file_children(db, rel_path, settings)
    db.execute_write(
        DELETE_FILE_NODE_QUERY,
        file_id=f"{settings.repo_name}:{rel_path}",
    )


def _is_excluded(rel_path: str, settings: Settings) -> bool:
    return any(part in settings.excludes for part in Path(rel_path).parts)


def sync_changes(
    db: GraphDB,
    settings: Settings = SETTINGS,
    *,
    base: str = "ORIG_HEAD",
    head: str = "HEAD",
    changes: list[Change] | None = None,
) -> SyncResult:
    started = time.monotonic()
    result = SyncResult()
    structure_changed = False
    changed_file_ids: set[str] = set()
    class_identifiers: set[str] = set()
    for change in changes if changes is not None else git_changes(settings, base, head):
        if change.status == "R" and change.old_path and change.old_path != change.path:
            class_identifiers.update(_file_class_identifiers(db, change.old_path, settings))
            delete_file(db, change.old_path, settings)
            result.deleted.append(change.old_path)
            structure_changed = True
            changed_file_ids.add(f"{settings.repo_name}:{change.old_path}")
        if _is_excluded(change.path, settings):
            # Keep incremental sync aligned with full scans and clean up any stale excluded node.
            class_identifiers.update(_file_class_identifiers(db, change.path, settings))
            delete_file(db, change.path, settings)
            result.skipped.append(change.path)
            structure_changed = True
            changed_file_ids.add(f"{settings.repo_name}:{change.path}")
            continue
        extension = Path(change.path).suffix.lower()
        if extension not in SOURCE_EXTENSIONS and not change.path.lower().endswith(".bpmn20.xml"):
            result.skipped.append(change.path)
            continue
        if change.status == "D":
            class_identifiers.update(_file_class_identifiers(db, change.path, settings))
            delete_file(db, change.path, settings)
            result.deleted.append(change.path)
            structure_changed = True
            changed_file_ids.add(f"{settings.repo_name}:{change.path}")
            continue
        source_path = settings.repo_root / change.path
        if not source_path.is_file():
            result.errors.append(f"{change.path}: file does not exist")
            continue
        try:
            old_class_identifiers = _file_class_identifiers(db, change.path, settings)
            parsed = parse_file(source_path, settings)
            clear_file_children(db, change.path, settings)
            ingest_files(db, [parsed], settings, reconcile=False)
            result.added_or_modified.append(change.path)
            structure_changed = True
            changed_file_ids.add(parsed.id)
            class_identifiers.update(old_class_identifiers)
            for symbol in parsed.classes:
                class_identifiers.update(
                    value
                    for value in (symbol.name, symbol.qualified_name, *symbol.aliases)
                    if value
                )
        except (OSError, SyntaxError, UnicodeError, ValueError) as exc:
            result.errors.append(f"{change.path}: {exc}")
    if structure_changed:
        reconcile_structural_links(
            db,
            file_ids=sorted(changed_file_ids),
            class_identifiers=sorted(class_identifiers),
        )
        stitch_endpoints(db, settings)
    result.duration_seconds = time.monotonic() - started
    return result
