# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Delegated: a zero-build HTML/CSS/JavaScript interface served by the existing Python graph-engine CLI. This keeps installation local, avoids a second application toolchain, and works on LAN-connected desktop and mobile browsers.

## Users

The primary user is a developer or AI-assisted engineering operator investigating structural and architectural relationships across one or more repositories.

## Product Purpose

Darul Graph Engine turns deterministic code evidence and human-validated operational knowledge into a shared Neo4j knowledge graph. It gives developers and coding agents compact, trustworthy traces across repositories without presenting a plausible match as established fact.

## Primary Outcome

A developer or coding agent can ask what happens from an entry point to its downstream effect and receive a bounded, evidence-backed trace across UI actions, services, APIs, messages, workflows, implementation, and architectural decisions. Missing or untrusted links remain explicit evidence gaps.

## Positioning

Darul is a deterministic context compiler for multi-repository software systems. Unlike a generic Neo4j browser or an AI-generated code map, it separates facts parsed from source, candidate connections inferred by matching, and operational knowledge confirmed by a human.

## Operating Context

The atlas runs from the same repository as the parser and connects through the existing graph database configuration. Its graph exploration is read-only. Guarded operator workflows may record service mappings, validation decisions, and repository operations. The product covers all ingested repositories and remains useful on both a workstation and a phone reached over the LAN.

## Trust Model

- `OBSERVED`: directly extracted from source, configuration, or BPMN evidence.
- `SUGGESTED`: an automatically inferred cross-boundary match awaiting review.
- `VALIDATED`: a human-confirmed mapping or connection with recorded provenance.
- `REJECTED`: a reviewed candidate that must not be proposed again unless its evidence materially changes.
- `STALE`: a previous validation whose source evidence or mapping inputs have changed.


## LLM Safety Boundary

- Autonomous context must fail closed: `SUGGESTED`, `REJECTED`, and `STALE` connections are excluded from definitive context.
- Suggestions may appear only in an explicitly requested diagnostic section and must never be phrased as established behavior.
- Every injected cross-repository connection must include its evidence class and provenance.
- Confidence scores may rank a review queue but can never promote a suggestion to `VALIDATED`.
- When validation is absent or stale, Darul reports an evidence gap instead of completing the path speculatively.
- Human assertions are versioned, auditable, portable, and projected into Neo4j rather than existing only as mutable graph state.

## Capabilities and Constraints

- Serve through `python3 -m graph_engine.cli visualize` without a frontend build step.
- Filter by repository, node type, and relationship type.
- Search nodes and inspect properties and immediate relationships.
- Navigate a responsive force-directed graph without mutating Neo4j.
- Review suggested cross-repository connections and validate, reject, or remap them through a guarded operator workflow.
- Store durable validation rules in a version-controlled `.darul/validations.yaml` file and project them into the graph.
- Preserve globally scoped repository entity IDs.
- Do not expose graph credentials to browser JavaScript.

## Evidence on Hand

- Parser output is structural evidence, not proof that two deployed systems communicate.
- Existing Neo4j structure, endpoint candidates, service mappings, decision lineage, and human validation records are distinct evidence sources.
- Runtime and deployment knowledge supplied by an informed operator is required when source code does not expose the actual service destination.

## Product Principles

- Begin with an engineering question, not database mechanics.
- Preserve the boundary between observation, inference, and human validation.
- Prefer an explicit missing edge over a plausible but unverified connection.
- Make trust and provenance machine-enforceable, not merely visual labels.
- Keep topology readable as graph density increases.
- Reveal detail on demand while preserving spatial context.
- Make filters and current scope continuously visible.
- Keep credentials and Cypher execution server-side.

## Success Measures

- At least 90% fewer model input tokens than conventional repository exploration on fixed benchmarks.
- At least 90% behavioral fact parity with targeted source inspection on the same benchmark workload.
- Zero unvalidated cross-repository suggestions represented as facts in default agent context.
- Every unresolved trace identifies the missing evidence or validation needed to continue.
- Typical incremental updates complete in under two seconds.

## Non-Goals

- Guessing runtime topology through model inference.
- Replacing compilers, language servers, or source-level debugging.
- Treating a similarity or confidence score as proof.
- Requiring operators to approve every endpoint when one bounded service mapping explains them.
- Becoming a generic Neo4j administration interface or hosted observability platform.

## Accessibility & Inclusion

The interface must support keyboard navigation, reduced motion, high-contrast labels, non-color node identification, and responsive layouts.
