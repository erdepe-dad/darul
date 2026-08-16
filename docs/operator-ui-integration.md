# Darul Operator UI Integration Contract

This document defines the contract between Darul and a separate operator UI. Darul is the source of truth for parsed evidence, graph state, trust decisions, and guarded operations. The UI is the human-in-the-loop operator surface and must not infer trusted topology independently.

## Runtime boundary

```text
Browser -> operator UI server (:4173) -> same-origin /api proxy
        -> Darul visualization/operator API (:38533) -> Neo4j
```

- The operator UI never receives Neo4j credentials and never writes Neo4j or Darul repository files directly.
- `server.py` forwards `/api/*` to Darul and keeps Darul bound to localhost by default.
- Read-only requests do not require the operator token.
- Mutations require `Authorization: Bearer <DARUL_OPERATOR_TOKEN>`.
- The browser stores the operator token only in `sessionStorage`.

## Ownership

| Concern | Darul owns | Operator UI owns |
| --- | --- | --- |
| Code and configuration parsing | Extraction, evidence location, safe redaction, repository-scoped IDs | Evidence presentation and navigation |
| Endpoint matching | Normalization and `SUGGESTED` candidates | Candidate review queue and ambiguity display |
| Trust | State transitions, provenance, stale detection, retrieval enforcement | Explicit operator intent and fail-closed rendering |
| Persistence | Neo4j and future `.darul/validations.yaml` projection | No independent topology persistence |
| Operations | Repository registry, build/sync execution, status and bounded logs | Start actions and poll/render status |
| Security | Mutation authentication, repository/path bounds, CORS policy | Same-origin proxy and session-only token handling |

## Trust contract

Every cross-repository connection must use one of these states:

| State | Meaning | Definitive agent context |
| --- | --- | --- |
| `OBSERVED` | Direct source, configuration, or BPMN evidence | Yes, as an observed fact only |
| `SUGGESTED` | Automatic match awaiting review | No |
| `VALIDATED` | Human-confirmed connection with provenance | Yes |
| `REJECTED` | Explicitly rejected candidate | No |
| `STALE` | Previous validation invalidated by changed evidence | No |

The operator UI must treat a missing or unknown trust state as unvalidated. A confidence score, unique route match, saved runtime service mapping, or successful stitch does not imply `VALIDATED`. Darul must enforce the same rule in API counts and agent-facing retrieval; UI copy and colors are not a security boundary.

## Current M1 API

The Darul visualization process serves the following API on port `38533`. The operator UI accesses it through its same-origin `/api` proxy.

| Method | Path | Authentication | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/health` | None | Darul and graph database health |
| `GET` | `/api/operator/overview` | None | Repository inventory, coverage, candidate counts, services, and operations |
| `GET` | `/api/operator/unresolved?repository=<name>&limit=<n>` | None | Requests without a `VALIDATED` backend route, including candidate evidence |
| `GET` | `/api/operator/operations` | None | Recent build/sync operation status |
| `POST` | `/api/operator/operations` | Bearer token | Start a registered repository `build` or `sync` |
| `POST` | `/api/operator/services` | Bearer token | Save an observed runtime service mapping and restitch candidates |
| `POST` | `/api/operator/services/clear` | Bearer token | Clear a runtime service mapping and restitch candidates |
| `GET` | `/api/meta`, `/api/graph`, `/api/trace`, `/api/node/<id>` | None | Read-only graph atlas data |

Important M1 response semantics:

- `requests` is the number of observed frontend/API requests.
- `candidates` counts requests with any inferred backend candidate.
- `resolved` counts only requests with a `TARGETS_ROUTE` relationship whose `trust_status` is `VALIDATED`.
- `unresolved` is `requests - resolved`; a request can be unresolved and still have candidates.
- `candidate_repo`, `candidate_route`, and `candidate_trust_status` describe review evidence, not confirmed topology.
- Service mapping mutations currently configure runtime mapping evidence. They do not create human-validation provenance and must not be presented as approval.

## Error contract

- Non-2xx JSON responses contain an `error` string.
- The operator UI must show API failures and retain the last clearly labelled state; it must not fabricate empty success data.
- `401` means the mutation token is missing or invalid.
- `503` means mutations are disabled because Darul has no operator token configured.
- `502` means Darul, Neo4j, or a guarded backend operation is unavailable.
- Unknown fields must be ignored for forward compatibility. Missing trust fields must fail closed.

## Parallel development workflow

Darul work can proceed independently when it preserves the documented response meaning. Operator-UI work can proceed against captured fixtures as long as those fixtures follow this contract.

1. Darul changes the API implementation and tests first when response meaning, authorization, or trust behavior changes.
2. Darul updates this contract in the same change and records whether the change is additive or breaking.
3. The operator UI may add support for additive fields without waiting for a coordinated release.
4. Breaking field removal, renaming, or meaning changes require coordinated changes in both repositories.
5. The operator UI must keep unknown-field tolerance and explicit loading, empty, unauthorized, unavailable, and malformed-response states.
6. Cross-project completion requires Darul tests, the UI's validation command, and one proxy smoke test with both services running.

For UI-first work, keep representative JSON fixtures in the UI repository under `tests/fixtures/`. Fixtures should cover no candidates, ambiguous candidates, validated routes, rejected routes, stale validations, unavailable Darul, and missing trust metadata.

## Planned HITL contract (not implemented)

Milestone 2 will add durable review APIs. Until these endpoints exist in Darul, the operator UI must not simulate validation by relabelling service mappings.

The planned workflow is:

```text
OBSERVED request + SUGGESTED candidate
  -> operator validates, rejects, or remaps
  -> Darul records actor, rationale, scope, revisions, and evidence fingerprint
  -> compatible routes are restitched
  -> changed evidence makes the rule STALE
  -> only VALIDATED routes enter definitive LLM context
```

The preferred approval unit is a bounded service mapping such as `sample-web:BACKEND_API_URL -> sample-api:/api`, with endpoint-level exceptions where needed. The final request and response schemas must be added here before an operator UI depends on them.

## Local integration setup

Start Darul:

```bash
cd /path/to/darul
export DARUL_OPERATOR_TOKEN='replace-with-a-random-token'
export DARUL_REPO_ROOTS='sample-web=/srv/sample-web,sample-api=/srv/sample-api'
.venv/bin/python3 -m graph_engine.cli visualize --host 127.0.0.1 --port 38533
```

Start the operator UI in another terminal:

```bash
cd /path/to/operator-ui
npm run dev
```

Open `http://127.0.0.1:4173` and apply the same operator token. For non-default locations, configure `DARUL_HTTP_HOST`, `DARUL_HTTP_PORT`, `OPERATOR_UI_HOST`, and `OPERATOR_UI_PORT`.

## Cross-project verification

- Overview distinguishes `candidates` from `resolved`.
- A `SUGGESTED` candidate remains unresolved and never appears as validated.
- Missing trust metadata renders as unvalidated.
- Build/sync mutations fail without a valid token and succeed only for registered repositories.
- The operator UI can recover visibly from Darul or Neo4j being unavailable.
- Tokens, database credentials, URL credentials, and query secrets do not appear in responses or logs.
- Default LLM context includes observed facts and `VALIDATED` routes only.
