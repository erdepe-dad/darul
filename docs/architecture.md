# Architecture

Darul converts repository structure and engineering history into a shared Neo4j graph. Parsing and matching are deterministic and run locally; Neo4j is used for identity, traversal, persistence, and cross-project retrieval.

## Modules

| Module | Responsibility |
| --- | --- |
| `.graph_engine/config.py` | Repository identity, environment loading, connection settings, and excluded directories |
| `.graph_engine/db.py` | Neo4j driver lifecycle, schema constraints, read/write wrappers, and batched writes |
| `.graph_engine/parser.py` | Python AST parsing and zero-token fallback parsers for JavaScript, TypeScript, Go, Java, Vaadin, and Flowable BPMN |
| `.graph_engine/stitcher.py` | URL normalization, frontend-to-backend route stitching, and page inspection |
| `.graph_engine/tracer.py` | Bounded view/service traversal and Mermaid rendering across synchronous and asynchronous boundaries |
| `.graph_engine/sync.py` | Incremental add/modify/delete/rename synchronization from a Git diff |
| `.graph_engine/hooks/event_logger.py` | Session-event storage and architectural-decision lineage |
| `.graph_engine/hooks/context_inject.py` | Active-decision and structural-hotspot retrieval for prompt context |
| `.graph_engine/visualizer.py` | Read-only HTTP/API server for the graph atlas |
| `.graph_engine/web/` | Zero-build HTML, CSS, font, and canvas visualization client |
| `graph_engine/` | Import bridge that makes the hidden implementation executable with `python -m` |

## Graph identity

Every repository has one unique `Repository.name`. File and symbol IDs are globally scoped so repositories can safely share one database:

```text
CodeFile:      {repository}:{relative_path}
Class:         {repository}:{relative_path}::{class_name}
Function:      {repository}:{relative_path}::{function_signature}
CallSite:      {repository}:{relative_path}::call:{line}:{target_type}.{target_method}
Page:          {repository}:{relative_path}
UIAction:      {repository}:{relative_path}::action:{component}:{event}:{line}
BackendRoute:  {repository}:{method}:{normalized_path}
WorkflowStart: {repository}:{relative_path}::process-start:{process_key}:{line}
Decision:      {repository}:{uuid}
Session:       {repository}:{external_session_id}
```

Do not change ID generation without a migration plan. IDs are referenced by decisions, events, endpoints, and incremental synchronization.

## Core graph

```text
(Repository)-[:CONTAINS]->(CodeFile)
(CodeFile)-[:DEFINES]->(Class|Function)
(Function|UIAction)-[:DECLARES_CALL]->(CallSite)
(Function|UIAction)-[:CALLS]->(Function)
(CodeFile)-[:CONTAINS]->(Page)
(CodeFile)-[:CONTAINS]->(UIAction)
(Page)-[:HAS_ACTION]->(UIAction)
(Function)-[:DECLARES_ACTION]->(UIAction)
(UIAction)-[:DECLARED_IN]->(Function)
(Page)-[:MAKES_REQUEST]->(APIEndpoint)
(APIEndpoint)-[:TARGETS_ROUTE]->(BackendRoute)
(Repository)-[:CONTAINS]->(BackendRoute)
(BackendRoute)-[:HANDLED_BY]->(Function)
(CodeFile)-[:IMPORTS]->(Class)
(CodeFile)-[:CONTAINS]->(WorkflowProcess)
(WorkflowProcess)-[:HAS_STEP]->(WorkflowStep)
(WorkflowStep)-[:NEXT]->(WorkflowStep)
(WorkflowStep)-[:INVOKES]->(Class)
(WorkflowStep)-[:CALLS]->(WorkflowProcess)
(Function)-[:DECLARES_START]->(WorkflowStart)
(WorkflowStart)-[:RESOLVES_TO]->(WorkflowProcess)
(Function)-[:STARTS_PROCESS]->(WorkflowProcess)
(Function)-[:PUBLISHES_TO]->(MessageChannel)
(MessageChannel)-[:ROUTES_TO|SAME_CHANNEL]->(MessageChannel)
(MessageChannel)-[:CONSUMED_BY]->(Function)
(CodeFile)-[:USES_SYSTEM]->(ExternalSystem)
(Repository)-[:HAS_SESSION]->(Session)
(Session)-[:RECORDED]->(SessionEvent)
(Session)-[:MADE_DECISION]->(Decision)
(Decision)-[:AFFECTS]->(CodeFile)
(Decision)-[:SUPERSEDES]->(Decision)
```

The schema creates unique constraints for repositories, files, symbols, pages, endpoints, routes, decisions, and sessions.

`UIAction.effects` stores bounded, deterministic component-state mutations such as enable, visibility, value, selection, and content changes found inside a listener. Component actions are retained even when their file is not itself a routable `Page`; `DECLARES_ACTION` allows a behavioral trace to enter those downstream listeners from the declaring function.

## Full build flow

1. Resolve the active Git root and repository namespace.
2. Walk supported source files while excluding generated and dependency directories.
3. Parse files locally into imports, symbols, requests, routes, message channels, framework metadata, and workflow references.
4. Ensure graph constraints exist.
5. Replace structural nodes owned by the active repository.
6. Batch-ingest files and children using globally scoped IDs.
7. Reconcile persisted workflow starts with matching BPMN processes across all repositories.
8. Normalize endpoint paths and create `TARGETS_ROUTE` relationships where method and path match.
9. Apply persisted `ExternalSystem.path_prefix` mappings for requests whose runtime base URL is represented by a configuration key.

The full build does not delete other repository roots.

## Incremental synchronization

`sync` reads a Git name-status diff. Deleted and renamed-away files have their owned structural subtree removed. Added, modified, and renamed-to files are reparsed individually and replaced. Imports, call sites, and workflow references are persisted during each file update, then graph-wide reconciliation and endpoint stitching run once after all changed subtrees are written. Java imports prefer a class in the source repository, then a unique cross-repository class with the exact qualified name.

The post-merge installer adds a marked block to `.git/hooks/post-merge`. Existing hook content is preserved, and repeated installation replaces only Darul's marked block.

## Parser strategy

Python uses the standard-library `ast` module. Other languages use bounded structural regular expressions designed for speed and graceful degradation. Bounded configuration inputs include properties, YAML, TOML, Gradle, Maven, Compose, package manifests, requirements, and Go modules. The fallback parsers are not full compilers and may miss heavily generated syntax, macros, dynamic routing, unusual decorators, or calls assembled across multiple expressions.

Each observed surrounding-system dependency becomes an `ExternalSystem` with `evidence_status = 'OBSERVED'` and a file-owned `USES_SYSTEM` relationship. Evidence records kind, technology, role, safe configuration key, source line, and runtime/test scope. The parser distinguishes Redis cache, datastore, session, and pub/sub evidence; identifies known email, communications, identity, storage, search, workflow, and payment integrations; and retains unknown literal HTTP destinations as host-and-port identities.

Java `@JsonApiResource` classes become backend routes only when the repository declares a CRNK or Katharsis server path prefix in a properties file. This prevents client-side JSON:API DTOs from being misclassified as servers. External configuration can supply the same evidence through `GRAPH_ENGINE_JSON_API_PREFIXES`.

Runtime service keys are represented by `ExternalSystem` nodes. The `services set` command stores the observed base URL, derived path prefix, and optional target repository on that node. Stitching then joins the prefixed request path to backend routes and marks the relationship with `resolution = 'configured-prefix'`; changing or clearing a mapping removes stale managed links on the next stitch pass.

Flowable BPMN uses the standard-library XML parser. Each process becomes a `WorkflowProcess`; tasks, events, and gateways become ordered `WorkflowStep` nodes. Runtime starts are persisted as `WorkflowStart` references, allowing `STARTS_PROCESS` links to be reconciled after the matching BPMN repository is scanned. Runtime starts, delegate expressions, listener classes, and called processes prefer matches in the source repository, then resolve a unique cross-repository match. Ambiguous cross-repository aliases and process keys remain unlinked instead of creating speculative edges.

When extending a parser:

1. Preserve globally scoped IDs.
2. Keep malformed files non-fatal and report errors in the scan result.
3. Add focused fixtures for extraction and false positives.
4. Avoid network calls or model inference.
5. Measure scan impact on a representative repository.

## Visualization API

The Python server owns the Neo4j connection. The browser can request metadata, a filtered graph, and one node's immediate neighborhood. Queries are fixed and parameterized; arbitrary Cypher is not accepted.

```text
GET /api/health
GET /api/meta
GET /api/graph?repository=...&labels=...&relationships=...&focus=...&depth=2
GET /api/trace?repository=...&view=...&path_limit=1200
GET /api/node/{element_id}
```

The web interface is read-only. It still exposes code paths, symbol names, routes, and decision text to anyone who can reach the HTTP server, so network authentication remains an operator responsibility.

## Decision lineage

A new decision can supersede an earlier decision. The old node becomes `SUPERSEDED`, the new node becomes `ACTIVE`, and `new-[:SUPERSEDES]->old` preserves lineage. Context retrieval returns active decisions and the titles they replaced.

## Target: human validation and agent trust

Static parsing can prove that a frontend declares a request and that a backend declares a compatible route. It cannot always prove that the deployed frontend actually targets that backend. Darul therefore treats cross-boundary stitching as a governed assertion rather than collapsing discovery and truth into one relationship.

The validation lifecycle is:

```text
OBSERVED source facts -> SUGGESTED connection -> VALIDATED or REJECTED
                                              -> STALE when evidence changes
```


```text
source repository + service key -> target repository + path prefix
```


Durable assertions will belong in a version-controlled `.darul/validations.yaml` file. Neo4j stores their projected nodes and relationships for traversal, including validator, timestamp, rationale, source revision, target revision, rule scope, and evidence fingerprint. A changed fingerprint marks the projection `STALE`; it does not silently reapprove the new evidence.

Agent-facing retrieval now fails closed for route candidates. Default context includes observed structural facts and `VALIDATED` route assertions; `SUGGESTED` routes are excluded from definitive paths. Diagnostic retrieval may show suggestions in a separately labeled section, but confidence alone never changes their trust state. An incomplete trusted path is returned as an evidence gap rather than completed speculatively.


## Extension points

- Add a language parser in `parser.py` and include its extensions in `SOURCE_EXTENSIONS`.
- Add framework metadata to `ParsedFile` without changing file identity.
- Add relationship-aware inspection in `stitcher.py` using parameterized Cypher.
- Add visualization taxonomy styles in `web/app.js` while preserving semantic node labels.
- Add database implementations behind the `GraphDB` interface, including any schema differences required by non-Neo4j engines.
