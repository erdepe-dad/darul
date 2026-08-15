"""View-centric behavioral trace extraction and diagram rendering."""

from __future__ import annotations

from typing import Any

from .config import SETTINGS, Settings
from .db import GraphDB


SEED_QUERY = """
MATCH (f:CodeFile {repo_name: $repo_name})
OPTIONAL MATCH (f)-[:CONTAINS]->(p:Page)
OPTIONAL MATCH (f)-[:DEFINES]->(c:Class)
WITH f, collect(DISTINCT p.route_path) AS routes, collect(DISTINCT c.name) AS classes,
     toLower($view) AS needle
WHERE toLower(f.path) CONTAINS needle
   OR any(route IN routes WHERE toLower(coalesce(route, '')) CONTAINS needle)
   OR any(name IN classes WHERE toLower(coalesce(name, '')) CONTAINS needle)
RETURN f.id AS id, f.path AS path, routes, classes,
       CASE
         WHEN any(name IN classes WHERE toLower(name) = needle) THEN 4
         WHEN any(route IN routes WHERE toLower(route) = needle OR toLower(route) = '/' + needle) THEN 3
         WHEN toLower(f.path) ENDS WITH '/' + needle + '.java' THEN 2
         ELSE 1
       END AS score
ORDER BY score DESC, size(f.path), f.path
LIMIT 1
"""


TRACE_SEED_QUERY = """
MATCH (seed:CodeFile {id: $seed_id})
RETURN elementId(seed) AS element_id, labels(seed) AS labels, properties(seed) AS properties
"""


TRACE_NEIGHBOR_QUERY = """
MATCH (source)
WHERE elementId(source) IN $source_ids
MATCH (source)-[rel]->(target)
WHERE type(rel) IN $relationship_types
RETURN elementId(source) AS source, labels(source) AS source_labels,
       properties(source) AS source_properties,
       elementId(target) AS target, labels(target) AS target_labels,
       properties(target) AS target_properties,
       elementId(rel) AS relationship_id, type(rel) AS relationship_type,
       properties(rel) AS relationship_properties
LIMIT $limit
"""


TRACE_RELATIONSHIPS = [
    "CONTAINS", "DEFINES", "HAS_ACTION", "DECLARES_ACTION", "CALLS", "MAKES_REQUEST",
    "TARGETS_ROUTE", "HANDLED_BY", "STARTS_PROCESS", "HAS_STEP", "NEXT", "INVOKES",
    "TARGETS_SYSTEM", "PUBLISHES_TO", "CONSUMED_BY", "ROUTES_TO", "SAME_CHANNEL",
    "DECLARES_START", "RESOLVES_TO",
]


def _label(properties: dict[str, Any], labels: list[str]) -> str:
    if "UIAction" in labels:
        event = str(properties.get("event") or "event").lower()
        component = str(properties.get("name") or "action").replace("_", " ").strip()
        words = component.split()
        if words and words[0].lower() in {"bt", "btn", "button"}:
            component = " ".join(words[1:]) or "button"
        elif component.lower() in {"grd", "grid"}:
            component = "grid row"
        verb = {
            "click": "Click", "itemclick": "Open", "selection": "Select",
            "valuechange": "Change", "attach": "Attach", "detach": "Detach",
        }.get(event, event.title())
        label = f"{verb} {component}".strip()
        effects = [str(effect) for effect in properties.get("effects") or [] if effect]
        return f"{label}\nEffects: {'; '.join(effects[:6])}" if effects else label
    if "APIEndpoint" in labels:
        return f"{properties.get('method', '?')} {properties.get('normalized_url', properties.get('url', ''))}"
    if "BackendRoute" in labels:
        return f"{properties.get('method', '?')} {properties.get('route_path', '')}"
    if "WorkflowStart" in labels:
        return f"Start {properties.get('process_key', 'unknown process')}"
    if "MessageChannel" in labels:
        return f"{properties.get('broker', 'message')} · {properties.get('channel', properties.get('name', 'channel'))}"
    return str(
        properties.get("name")
        or properties.get("process_key")
        or properties.get("path")
        or properties.get("qualified_name")
        or properties.get("id")
        or "Node"
    )


def _lane(
    labels: list[str], properties: dict[str, Any], source_repo: str, source_is_ui: bool,
) -> str:
    if "WorkflowProcess" in labels or "WorkflowStep" in labels or "WorkflowStart" in labels:
        return "Workflow"
    if "ExternalSystem" in labels:
        return "Systems"
    if "MessageChannel" in labels:
        return "Messaging"
    if "BackendRoute" in labels:
        return "Backend"
    if "UIAction" in labels or "Page" in labels:
        return "Vaadin UI"
    if properties.get("repo_name") == source_repo:
        return "Frontend services" if source_is_ui else "Backend"
    if "Function" in labels or "Class" in labels or "CodeFile" in labels:
        return "Backend"
    return "Systems"


def _request_resolution_warning(
    nodes: dict[str, dict[str, Any]], links: list[dict[str, Any]],
) -> str | None:
    endpoints = [
        node for node in nodes.values()
        if "APIEndpoint" in node["labels"]
    ]
    if not endpoints:
        return None
    resolved = {
        link["source"] for link in links
        if link["type"] == "TARGETS_ROUTE"
    }
    unresolved = [node for node in endpoints if node["id"] not in resolved]
    if not unresolved:
        return None
    if len(unresolved) == len(endpoints):
        return "No API request in this trace resolves to an ingested backend route."
    labels = [node["label"] for node in unresolved[:3]]
    detail = "; ".join(labels)
    if len(unresolved) > len(labels):
        detail += f"; +{len(unresolved) - len(labels)} more"
    return (
        f"{len(unresolved)} of {len(endpoints)} API requests do not resolve to an "
        f"ingested backend route: {detail}."
    )


def _workflow_resolution_warning(
    nodes: dict[str, dict[str, Any]], links: list[dict[str, Any]],
) -> str | None:
    starts = [
        node for node in nodes.values()
        if "WorkflowStart" in node["labels"]
    ]
    if not starts:
        return None
    resolved = {
        link["source"] for link in links
        if link["type"] == "RESOLVES_TO"
    }
    unresolved = [node for node in starts if node["id"] not in resolved]
    if not unresolved:
        return None
    keys = [str(node["properties"].get("process_key") or "unknown") for node in unresolved[:3]]
    detail = "; ".join(keys)
    if len(unresolved) > len(keys):
        detail += f"; +{len(unresolved) - len(keys)} more"
    if len(unresolved) == len(starts):
        return f"No workflow start in this trace resolves to an ingested BPMN process: {detail}."
    return (
        f"{len(unresolved)} of {len(starts)} workflow starts do not resolve to an "
        f"ingested BPMN process: {detail}."
    )


def trace_view(
    db: GraphDB,
    view: str,
    settings: Settings = SETTINGS,
    *,
    path_limit: int = 1200,
) -> dict[str, Any]:
    seeds = db.execute_read(SEED_QUERY, repo_name=settings.repo_name, view=view.strip())
    if not seeds:
        return {
            "view": view,
            "repository": settings.repo_name,
            "found": False,
            "nodes": [],
            "links": [],
            "lanes": [],
            "warnings": ["No matching page, class, or source file was found."],
        }
    seed = seeds[0]
    source_is_ui = bool(seed.get("routes"))
    edge_limit = max(100, min(path_limit, 3000))
    seed_rows = db.execute_read(TRACE_SEED_QUERY, seed_id=seed["id"])
    nodes_by_id: dict[str, dict[str, Any]] = {}
    links_by_id: dict[str, dict[str, Any]] = {}

    def add_node(element_id: str, labels: list[str], properties: dict[str, Any]) -> None:
        nodes_by_id[element_id] = {
            "id": element_id,
            "entity_id": properties.get("id"),
            "label": _label(properties, labels),
            "labels": labels,
            "properties": properties,
            "lane": _lane(labels, properties, settings.repo_name, source_is_ui),
        }

    for row in seed_rows:
        add_node(row["element_id"], row.get("labels") or [], row.get("properties") or {})
    frontier = [row["element_id"] for row in seed_rows]
    expanded: set[str] = set()
    truncated = False
    for _ in range(10):
        source_ids = [node_id for node_id in frontier if node_id not in expanded]
        if not source_ids or len(links_by_id) >= edge_limit:
            break
        expanded.update(source_ids)
        remaining = edge_limit - len(links_by_id)
        rows = db.execute_read(
            TRACE_NEIGHBOR_QUERY,
            source_ids=source_ids,
            relationship_types=TRACE_RELATIONSHIPS,
            limit=remaining,
        )
        frontier = []
        for row in rows:
            add_node(row["source"], row.get("source_labels") or [], row.get("source_properties") or {})
            add_node(row["target"], row.get("target_labels") or [], row.get("target_properties") or {})
            links_by_id[row["relationship_id"]] = {
                "id": row["relationship_id"],
                "source": row["source"],
                "target": row["target"],
                "type": row["relationship_type"],
                "properties": row.get("relationship_properties") or {},
            }
            if row["target"] not in expanded:
                frontier.append(row["target"])
        if len(rows) >= remaining:
            truncated = True
            break
    useful_labels = {
        "Page", "UIAction", "Function", "APIEndpoint", "BackendRoute",
        "WorkflowProcess", "WorkflowStep", "WorkflowStart", "ExternalSystem", "MessageChannel",
    }
    useful_nodes = {
        node_id: node
        for node_id, node in nodes_by_id.items()
        if useful_labels.intersection(node["labels"])
    }
    useful_links = [
        link for link in links_by_id.values()
        if link["source"] in useful_nodes and link["target"] in useful_nodes
    ]
    # Keep structural bridge nodes only when they connect two visible behavioral nodes.
    visible_ids = set(useful_nodes)
    for link in links_by_id.values():
        if link["source"] in visible_ids and link["target"] in nodes_by_id:
            target = nodes_by_id[link["target"]]
            if "Class" in target["labels"]:
                useful_nodes[target["id"]] = target
                useful_links.append(link)

    outgoing: dict[str, int] = {}
    for link in useful_links:
        if link["type"] == "NEXT":
            outgoing[link["source"]] = outgoing.get(link["source"], 0) + 1
    for link in useful_links:
        props = link["properties"]
        link["alternative"] = bool(
            props.get("condition") or props.get("is_default") or outgoing.get(link["source"], 0) > 1
        )

    worker_ids = {link["target"] for link in useful_links if link["type"] == "CONSUMED_BY"}
    for _ in range(10):
        discovered = {
            link["target"]
            for link in useful_links
            if link["type"] == "CALLS" and link["source"] in worker_ids
            and "Function" in useful_nodes.get(link["target"], {}).get("labels", [])
        }
        if discovered.issubset(worker_ids):
            break
        worker_ids.update(discovered)
    for node_id in worker_ids:
        if node_id in useful_nodes:
            useful_nodes[node_id]["lane"] = "Workers"

    lane_order = (
        ("Vaadin UI", "Frontend services", "Messaging", "Backend", "Workers", "Workflow", "Systems")
        if source_is_ui
        else ("Backend", "Messaging", "Workers", "Workflow", "Systems")
    )
    lanes = [
        {"name": name, "nodes": [node["id"] for node in useful_nodes.values() if node["lane"] == name]}
        for name in lane_order
    ]
    lanes = [lane for lane in lanes if lane["nodes"]]
    warnings: list[str] = []
    if request_warning := _request_resolution_warning(useful_nodes, useful_links):
        warnings.append(request_warning)
    if workflow_warning := _workflow_resolution_warning(useful_nodes, useful_links):
        warnings.append(workflow_warning)
    if truncated:
        warnings.append("The trace reached its path limit and may be incomplete.")
    result = {
        "view": view,
        "repository": settings.repo_name,
        "found": True,
        "seed": seed,
        "nodes": list(useful_nodes.values()),
        "links": useful_links,
        "lanes": lanes,
        "warnings": warnings,
        "stats": {
            "nodes": len(useful_nodes),
            "links": len(useful_links),
            "alternatives": sum(1 for link in useful_links if link["alternative"]),
        },
    }
    result["mermaid"] = render_mermaid(result)
    return result


def render_mermaid(trace: dict[str, Any]) -> str:
    nodes = trace.get("nodes") or []
    links = trace.get("links") or []
    aliases = {node["id"]: f"n{index}" for index, node in enumerate(nodes)}
    lines = ["flowchart LR"]
    for lane in trace.get("lanes") or []:
        lane_id = lane["name"].lower().replace(" ", "_")
        lines.append(f'  subgraph {lane_id}["{lane["name"]}"]')
        for node_id in lane["nodes"]:
            node = next(item for item in nodes if item["id"] == node_id)
            label_limit = 260 if "UIAction" in node["labels"] else 90
            label = node["label"].replace('"', "'").replace("\n", " ")[:label_limit]
            lines.append(f'    {aliases[node_id]}["{label}"]')
        lines.append("  end")
    for link in links:
        source = aliases.get(link["source"])
        target = aliases.get(link["target"])
        if not source or not target:
            continue
        props = link.get("properties") or {}
        detail = props.get("condition") or props.get("name") or ("default" if props.get("is_default") else "")
        label = str(detail or link["type"]).replace('"', "'").replace("\n", " ")[:240]
        arrow = "-.->" if link.get("alternative") else "-->"
        lines.append(f'  {source} {arrow}|"{label}"| {target}')
    return "\n".join(lines)
