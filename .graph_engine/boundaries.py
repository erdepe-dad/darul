"""Repository-wide evidence report for external system boundaries."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .config import SETTINGS, Settings
from .parser import ScanResult


def build_boundary_report(
    scan: ScanResult, settings: Settings = SETTINGS,
) -> dict[str, Any]:
    http_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unresolved_http: list[dict[str, Any]] = []
    systems: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    messaging: dict[tuple[str, str, str], dict[str, Any]] = {}

    for parsed in scan.files:
        for request in parsed.requests:
            evidence = {
                "method": request.method,
                "path": request.normalized_url,
                "source_file": parsed.path,
                "source_function_id": request.source_function_id or None,
                "line": request.line,
            }
            if request.system:
                http_groups[request.system].append(evidence)
            else:
                unresolved_http.append(evidence)

        for dependency in parsed.system_dependencies:
            key = (
                dependency.kind,
                dependency.name,
                dependency.technology,
                dependency.role,
            )
            row = systems.setdefault(
                key,
                {
                    "name": dependency.name,
                    "kind": dependency.kind,
                    "technology": dependency.technology,
                    "role": dependency.role,
                    "evidence_status": "OBSERVED",
                    "evidence": [],
                },
            )
            row["evidence"].append(
                {
                    "source_file": parsed.path,
                    "line": dependency.line,
                    "scope": dependency.scope,
                    "config_key": dependency.config_key or None,
                    "description": dependency.evidence,
                }
            )

        for use in parsed.message_uses:
            key = (use.broker, use.channel, use.direction)
            row = messaging.setdefault(
                key,
                {
                    "broker": use.broker,
                    "channel": use.channel,
                    "direction": use.direction,
                    "evidence_status": "OBSERVED",
                    "evidence": [],
                },
            )
            row["evidence"].append(
                {
                    "source_file": parsed.path,
                    "source_function_id": use.source_id,
                    "line": use.line,
                }
            )

    http_services = [
        {
            "name": name,
            "kind": "http-service",
            "evidence_status": "OBSERVED",
            "request_count": len(evidence),
            "requests": sorted(
                evidence,
                key=lambda item: (item["source_file"], item["line"], item["method"], item["path"]),
            ),
        }
        for name, evidence in sorted(http_groups.items(), key=lambda item: item[0].lower())
    ]
    system_rows = sorted(
        systems.values(),
        key=lambda item: (item["kind"], item["name"].lower(), item["role"]),
    )
    message_rows = sorted(
        messaging.values(),
        key=lambda item: (item["broker"], item["channel"], item["direction"]),
    )
    return {
        "repository": settings.repo_name,
        "root": str(settings.repo_root),
        "evidence_policy": "observed-only; backend repository matches require validation",
        "files_scanned": len(scan.files),
        "duration_seconds": round(scan.duration_seconds, 4),
        "http_services": http_services,
        "systems": system_rows,
        "messaging": message_rows,
        "unresolved_http": sorted(
            unresolved_http,
            key=lambda item: (item["source_file"], item["line"], item["method"], item["path"]),
        ),
        "errors": scan.errors,
        "summary": {
            "http_services": len(http_services),
            "http_requests": sum(item["request_count"] for item in http_services),
            "unresolved_http_requests": len(unresolved_http),
            "surrounding_systems": len(system_rows),
            "message_channels": len(message_rows),
        },
    }


def render_boundary_text(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        f"SYSTEM BOUNDARIES: {report['repository']}",
        "Evidence policy: observed code/configuration only; repository targets require validation.",
        (
            f"HTTP services: {summary['http_services']} | "
            f"requests: {summary['http_requests']} | "
            f"unresolved requests: {summary['unresolved_http_requests']}"
        ),
    ]
    if report["http_services"]:
        lines.append("\n[HTTP Services]")
        for service in report["http_services"]:
            lines.append(f"- {service['name']} ({service['request_count']} requests) [OBSERVED]")
            for request in service["requests"][:8]:
                lines.append(
                    f"  {request['method']} {request['path']} - "
                    f"{request['source_file']}:{request['line']}"
                )
            if service["request_count"] > 8:
                lines.append(f"  ... {service['request_count'] - 8} more")
    if report["systems"]:
        lines.append("\n[Surrounding Systems]")
        for system in report["systems"]:
            lines.append(
                f"- {system['name']} - {system['kind']}/{system['role']} [OBSERVED]"
            )
            for evidence in system["evidence"][:4]:
                scope = f" ({evidence['scope']})" if evidence.get("scope") else ""
                lines.append(
                    f"  {evidence['source_file']}:{evidence['line']}{scope} - "
                    f"{evidence['description']}"
                )
    if report["messaging"]:
        lines.append("\n[Messaging Channels]")
        for channel in report["messaging"]:
            lines.append(
                f"- {channel['broker']}:{channel['channel']} "
                f"({channel['direction']}) [OBSERVED]"
            )
    if report["unresolved_http"]:
        lines.append("\n[Unresolved HTTP Destinations]")
        for request in report["unresolved_http"][:20]:
            lines.append(
                f"- {request['method']} {request['path']} - "
                f"{request['source_file']}:{request['line']}"
            )
        if len(report["unresolved_http"]) > 20:
            lines.append(f"- ... {len(report['unresolved_http']) - 20} more")
    if report["errors"]:
        lines.append("\n[Parser Errors]")
        lines.extend(f"- {error}" for error in report["errors"])
    return "\n".join(lines)
