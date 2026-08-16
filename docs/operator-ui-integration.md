

## Runtime boundary

```text
        -> Darul visualization/operator API (:38533) -> Neo4j
```

- `server.py` forwards `/api/*` to Darul and keeps Darul bound to localhost by default.
- Read-only requests do not require the operator token.
- Mutations require `Authorization: Bearer <DARUL_OPERATOR_TOKEN>`.
- The browser stores the operator token only in `sessionStorage`.

## Ownership

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


## Current M1 API


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
- `401` means the mutation token is missing or invalid.
- `503` means mutations are disabled because Darul has no operator token configured.
- `502` means Darul, Neo4j, or a guarded backend operation is unavailable.
- Unknown fields must be ignored for forward compatibility. Missing trust fields must fail closed.

## Parallel development workflow


1. Darul changes the API implementation and tests first when response meaning, authorization, or trust behavior changes.
2. Darul updates this contract in the same change and records whether the change is additive or breaking.
4. Breaking field removal, renaming, or meaning changes require coordinated changes in both repositories.


## Planned HITL contract (not implemented)


The planned workflow is:

```text
OBSERVED request + SUGGESTED candidate
  -> operator validates, rejects, or remaps
  -> Darul records actor, rationale, scope, revisions, and evidence fingerprint
  -> compatible routes are restitched
  -> changed evidence makes the rule STALE
  -> only VALIDATED routes enter definitive LLM context
```


## Local integration setup

Start Darul:

```bash
export DARUL_OPERATOR_TOKEN='replace-with-a-random-token'
.venv/bin/python3 -m graph_engine.cli visualize --host 127.0.0.1 --port 38533
```


```bash
npm run dev
```


## Cross-project verification

- Overview distinguishes `candidates` from `resolved`.
- A `SUGGESTED` candidate remains unresolved and never appears as validated.
- Missing trust metadata renders as unvalidated.
- Build/sync mutations fail without a valid token and succeed only for registered repositories.
- Tokens, database credentials, URL credentials, and query secrets do not appear in responses or logs.
- Default LLM context includes observed facts and `VALIDATED` routes only.
