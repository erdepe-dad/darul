# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Delegated: a zero-build HTML/CSS/JavaScript interface served by the existing Python graph-engine CLI. This keeps installation local, avoids a second application toolchain, and works on LAN-connected desktop and mobile browsers.

## Users

The primary user is a developer or AI-assisted engineering operator investigating structural and architectural relationships across one or more repositories.

## Product Purpose

Darul Graph Engine parses source code and engineering decisions into a shared Neo4j knowledge graph. The visualization makes that machine-oriented graph legible to people for tracing dependencies, API paths, implementation ownership, and decision lineage.

## Positioning

Unlike a generic Neo4j administration browser, the explorer is organized around codebase questions: which files define a symbol, what request reaches which backend route, and what active decision affects a structural hotspot.

## Operating Context

The explorer runs from the same repository as the parser and connects through the existing graph database configuration. It is read-only, covers all ingested repositories, and is expected to remain useful on both a workstation and a phone reached over the LAN.

## Capabilities and Constraints

- Serve through `python3 -m graph_engine.cli visualize` without a frontend build step.
- Filter by repository, node type, and relationship type.
- Search nodes and inspect properties and immediate relationships.
- Navigate a responsive force-directed graph without mutating Neo4j.
- Preserve globally scoped repository entity IDs.
- Do not expose graph credentials to browser JavaScript.

## Evidence on Hand

The existing Neo4j graph schema, parser output, endpoint stitching, decision lineage, and context retrieval code are the factual data sources. No existing visual identity or interface assets were supplied.

## Product Principles

- Begin with an engineering question, not database mechanics.
- Keep topology readable as graph density increases.
- Reveal detail on demand while preserving spatial context.
- Make filters and current scope continuously visible.
- Keep credentials and Cypher execution server-side.

## Accessibility & Inclusion

The interface must support keyboard navigation, reduced motion, high-contrast labels, non-color node identification, and responsive layouts.
