"""Command-line interface for the structural knowledge graph engine."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import stat
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .boundaries import build_boundary_report, render_boundary_text
from .config import SETTINGS
from .db import GraphDB, GraphEngineError
from .hooks.event_logger import record_decision
from .parser import build_graph, scan_repository
from .stitcher import (
    clear_service,
    configure_service,
    inspect_page,
    list_services,
    stitch_endpoints,
)
from .sync import sync_changes
from .tracer import trace_view


def _json_default(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, default=_json_default))


def command_build(args: argparse.Namespace) -> int:
    if args.dry_run:
        result = scan_repository()
        _print_json(
            {
                "repo": SETTINGS.repo_name,
                "root": str(SETTINGS.repo_root),
                "files": len(result.files),
                "symbols": result.symbol_count,
                "workflow_processes": sum(len(item.workflow_processes) for item in result.files),
                "workflow_steps": sum(len(item.workflow_steps) for item in result.files),
                "observed_systems": sum(len(item.system_dependencies) for item in result.files),
                "skipped": result.skipped_files,
                "errors": result.errors,
                "duration_seconds": round(result.duration_seconds, 4),
                "ingested": False,
            }
        )
        return 0 if not result.errors else 1
    with GraphDB() as db:
        result = build_graph(db)
        stitched = stitch_endpoints(db)
    _print_json(
        {
            "repo": SETTINGS.repo_name,
            "files": len(result.files),
            "symbols": result.symbol_count,
            "requests": sum(len(item.requests) for item in result.files),
            "routes": sum(len(item.routes) for item in result.files),
            "workflow_processes": sum(len(item.workflow_processes) for item in result.files),
            "workflow_steps": sum(len(item.workflow_steps) for item in result.files),
            "observed_systems": sum(len(item.system_dependencies) for item in result.files),
            "stitched": stitched,
            "skipped": result.skipped_files,
            "errors": result.errors,
            "duration_seconds": round(result.duration_seconds, 4),
            "ingested": True,
        }
    )
    return 0 if not result.errors else 1


def command_sync(args: argparse.Namespace) -> int:
    with GraphDB() as db:
        result = sync_changes(db, base=args.base, head=args.head)
    _print_json(asdict(result))
    return 0 if not result.errors else 1


def command_inspect(args: argparse.Namespace) -> int:
    with GraphDB() as db:
        result = inspect_page(db, args.page)
    _print_json(result)
    return 0


def command_trace(args: argparse.Namespace) -> int:
    with GraphDB() as db:
        result = trace_view(db, args.view, path_limit=args.path_limit)
    if args.format == "mermaid":
        print(result.get("mermaid", ""))
    else:
        _print_json(result)
    return 0 if result.get("found") else 1


def command_boundaries(args: argparse.Namespace) -> int:
    result = scan_repository()
    report = build_boundary_report(result)
    if args.format == "text":
        print(render_boundary_text(report))
    else:
        _print_json(report)
    return 0 if not result.errors else 1


def command_install_hooks(args: argparse.Namespace) -> int:
    git_dir_result = __import__("subprocess").run(
        ["git", "-C", str(SETTINGS.repo_root), "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    if git_dir_result.returncode:
        raise GraphEngineError(f"{SETTINGS.repo_root} is not a Git repository")
    git_dir = Path(git_dir_result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = SETTINGS.repo_root / git_dir
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "post-merge"
    marker_start = "# >>> graph-engine >>>"
    marker_end = "# <<< graph-engine <<<"
    python = shlex.quote(sys.executable)
    root = shlex.quote(str(SETTINGS.repo_root))
    invocation = (
        f"if ! (cd {root} && {python} -m graph_engine.cli sync --base ORIG_HEAD --head HEAD); "
        "then echo 'graph-engine post-merge sync failed' >&2; fi"
    )
    block = f"{marker_start}\n{invocation}\n{marker_end}\n"
    existing = hook_path.read_text(encoding="utf-8") if hook_path.exists() else "#!/bin/sh\n"
    if marker_start in existing and marker_end in existing:
        prefix, tail = existing.split(marker_start, 1)
        _, suffix = tail.split(marker_end, 1)
        content = prefix + block + suffix.lstrip("\n")
    else:
        lines = existing.splitlines(keepends=True)
        if lines and lines[0].startswith("#!"):
            content = lines[0] + block + "".join(lines[1:]).lstrip("\n")
        else:
            content = "#!/bin/sh\n" + block + existing.lstrip("\n")
    hook_path.write_text(content, encoding="utf-8")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"Installed graph sync hook at {hook_path}")
    return 0


def command_decision(args: argparse.Namespace) -> int:
    with GraphDB() as db:
        decision_id = record_decision(
            db,
            args.title,
            args.rationale,
            args.file,
            session=args.session,
            supersedes=args.supersedes,
        )
    print(decision_id)
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {
        "repo_name": SETTINGS.repo_name,
        "repo_root": str(SETTINGS.repo_root),
        "uri": SETTINGS.uri,
        "git_repository": (SETTINGS.repo_root / ".git").exists(),
    }
    try:
        with GraphDB() as db:
            report["database"] = db.healthcheck()
        report["ok"] = True
    except GraphEngineError as exc:
        report["ok"] = False
        report["database_error"] = str(exc)
    _print_json(report)
    return 0 if report["ok"] else 1


def command_visualize(args: argparse.Namespace) -> int:
    from .visualizer import serve_visualization

    serve_visualization(host=args.host, port=args.port, open_browser=args.open)
    return 0


def command_service_list(args: argparse.Namespace) -> int:
    with GraphDB() as db:
        rows = list_services(db, args.repo or "")
    _print_json(rows)
    return 0


def command_service_set(args: argparse.Namespace) -> int:
    repo_name = args.repo or SETTINGS.repo_name
    with GraphDB() as db:
        service = configure_service(
            db,
            args.key,
            args.base_url,
            repo_name=repo_name,
            target_repo=args.target_repo or "",
        )
        stitched = stitch_endpoints(db)
    _print_json({"service": service, "stitched": stitched})
    return 0


def command_service_clear(args: argparse.Namespace) -> int:
    repo_name = args.repo or SETTINGS.repo_name
    with GraphDB() as db:
        service = clear_service(db, args.key, repo_name=repo_name)
        stitched = stitch_endpoints(db)
    _print_json({"service": service, "stitched": stitched})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="graph-engine", description=__doc__)
    parser.add_argument("--version", action="version", version="graph-engine 0.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Scan and ingest the complete repository")
    build.add_argument("--dry-run", action="store_true", help="Parse locally without connecting to the graph")
    build.set_defaults(handler=command_build)

    sync = subparsers.add_parser("sync", help="Apply an incremental Git diff")
    sync.add_argument("--base", default="ORIG_HEAD")
    sync.add_argument("--head", default="HEAD")
    sync.set_defaults(handler=command_sync)

    inspect = subparsers.add_parser("inspect", help="Extract the E2E subgraph for a page")
    inspect.add_argument("--page", required=True)
    inspect.set_defaults(handler=command_inspect)

    trace = subparsers.add_parser("trace", help="Trace a view through services, routes, and workflows")
    trace.add_argument("--view", required=True, help="Vaadin route, class name, or source path")
    trace.add_argument("--format", choices=("json", "mermaid"), default="json")
    trace.add_argument("--path-limit", type=int, default=1200)
    trace.set_defaults(handler=command_trace)

    boundaries = subparsers.add_parser(
        "boundaries", help="Report observed external services and surrounding systems"
    )
    boundaries.add_argument("--format", choices=("json", "text"), default="text")
    boundaries.set_defaults(handler=command_boundaries)

    install = subparsers.add_parser("install-hooks", help="Install the post-merge Git hook")
    install.set_defaults(handler=command_install_hooks)

    decision = subparsers.add_parser("decision", help="Record an architectural decision")
    decision.add_argument("--title", required=True)
    decision.add_argument("--rationale", required=True)
    decision.add_argument("--file", action="append", default=[])
    decision.add_argument("--session", default=os.getenv("CLAUDE_SESSION_ID", "standalone"))
    decision.add_argument("--supersedes")
    decision.set_defaults(handler=command_decision)

    doctor = subparsers.add_parser("doctor", help="Check repository and database connectivity")
    doctor.set_defaults(handler=command_doctor)

    visualize = subparsers.add_parser("visualize", help="Open the read-only graph explorer")
    visualize.add_argument("--host", default="127.0.0.1", help="Bind address; use 0.0.0.0 for LAN access")
    visualize.add_argument("--port", type=int, default=38533)
    visualize.add_argument("--open", action="store_true", help="Open the default browser")
    visualize.set_defaults(handler=command_visualize)

    services = subparsers.add_parser(
        "services", help="Manage runtime service base URLs used for endpoint stitching"
    )
    service_commands = services.add_subparsers(dest="service_command", required=True)

    service_list = service_commands.add_parser("list", help="List discovered service keys")
    service_list.add_argument("--repo", help="Limit results to one repository")
    service_list.set_defaults(handler=command_service_list)

    service_set = service_commands.add_parser("set", help="Configure a service base URL")
    service_set.add_argument("--key", required=True, help="Runtime key such as BACKEND_API_URL")
    service_set.add_argument("--base-url", required=True)
    service_set.add_argument("--repo", help="Source repository; defaults to the active repository")
    service_set.add_argument("--target-repo", help="Restrict matches to one backend repository")
    service_set.set_defaults(handler=command_service_set)

    service_clear = service_commands.add_parser("clear", help="Remove a service URL mapping")
    service_clear.add_argument("--key", required=True)
    service_clear.add_argument("--repo", help="Source repository; defaults to the active repository")
    service_clear.set_defaults(handler=command_service_clear)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except GraphEngineError as exc:
        print(f"graph-engine: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("graph-engine: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
