# Darul Milestones

## Milestone 1: Evidence-True System Boundary Map

### Goal

Darul correctly parses what a repository says about its own behavior and produces a repository or entry-point view of the systems it touches. Milestone 1 does not attempt to prove that an external service is implemented by a particular repository.

For a frontend or service repository, the output should answer:

- Which external HTTP services does this code call?
- Which method, path, configuration key, source function, file, and line provide the evidence?
- Which databases, caches, message brokers, workflow engines, object stores, search systems, identity providers, email systems, or other surrounding systems are declared or used?
- Which values are literal, configuration-based, resolved aliases, or dynamic and unresolved?

The governing rule is: report what the code proves and preserve what it does not prove.

### Required evidence classes

| Boundary | Minimum evidence |
| --- | --- |
| HTTP service | Method, path or URL expression, service/configuration key when present, source function, file, and line |
| Relational database | Engine when declared, such as PostgreSQL, MySQL, MariaDB, Oracle, or SQL Server; configuration source; access library or ORM; code location |
| Document/graph database | Engine when declared, such as MongoDB or Neo4j; configuration source; client or repository evidence; code location |
| Cache/key-value store | Engine such as Redis or Memcached, plus whether code evidence indicates cache, datastore, session, or pub/sub usage |
| Messaging | Broker, channel/topic/queue when statically available, publish or consume direction, source function, file, and line |
| Other surrounding system | Evidence-backed kind and identity for storage, search, email, identity, analytics, workflow, or another external boundary |

Secrets must never be stored or emitted. Configuration evidence records keys, schemes, drivers, hosts only when safe, and source locations; it excludes passwords, tokens, and credentials.

### Output contract

- `OBSERVED` means the boundary is directly supported by code or configuration evidence.
- Dynamic or incomplete values remain explicitly unresolved.
- A service/configuration key such as `BACKEND_API_URL` is a valid system identity when that is all the code provides.
- A normalized-route match may be shown as a candidate, but Milestone 1 output must not state that it reaches a particular backend repository without a valid human assertion.
- Every system and interaction links back to its source evidence.
- Repository-wide output includes systems not reached from one selected UI trace.
- Entry-point output includes the subset reachable from that seed and identifies traversal limits.

### Acceptance scenarios

1. Scanning a representative service repository reports observed HTTP service keys and request paths without claiming an unvalidated target repository.
2. Scanning a repository with a declared JDBC driver or datasource reports the database engine and evidence scope, including whether the declaration is under test or production configuration.
3. Scanning Redis cache/datastore use distinguishes it from Redis pub/sub messaging.
4. Scanning Kafka, RabbitMQ, JMS, or Spring Cloud Stream reports observed channels and direction without inventing dynamic names.
5. Scanning a literal external URL reports its hostname or declared system identity even when it does not use a recognized HTTP client wrapper.
6. False-positive service identities are rejected by fixtures and representative-repository checks.
7. The repository system-boundary report is deterministic, contains no secrets, and clearly separates observed evidence from unresolved values and suggested matches.
8. Scanning a monorepo preserves frontend, backend, worker, database, and infrastructure evidence instead of treating the repository as a frontend-only application.

### Current audit: 2026-08-16

Status: **COMPLETE**

| Capability | Status | Current evidence |
| --- | --- | --- |
| Local deterministic repository scan | Pass | Representative source and configuration fixtures parse without errors or network access |
| Java HTTP request extraction | Pass | Literal, configuration-backed, helper-composed, and unresolved request forms retain source provenance |
| Configuration-key service identity | Pass | Safe service configuration keys remain visible while arbitrary receiver and utility names are not promoted |
| Entry-point system lane | Pass | Entry-point traces render observed service keys and label automatic backend matches `SUGGESTED TARGET` |
| Literal/general external URL discovery | Pass | Evidence-backed literal hosts and local host-and-port process boundaries remain visible |
| Database engine discovery | Pass | MySQL is reported from Maven and datasource evidence with runtime/test scope and safe configuration keys |
| Redis and cache role discovery | Pass | Spring Cache is not mislabeled as Redis; fixtures distinguish Redis cache, session, datastore, and pub/sub evidence |
| Messaging discovery | Pass | Fixtures cover Kafka, RabbitMQ, Redis pub/sub, JMS, Spring Cloud Stream, direction, channels, and RabbitMQ bindings |
| General surrounding-system taxonomy | Pass | The model covers database, cache, messaging, workflow, storage, search, email, identity, communications, payment, and generic HTTP boundaries |
| Split SMTP configuration identity | Pass | Literal mail host and port properties are combined into a safe destination identity such as `localhost:1025` without retaining credentials |
| Source provenance | Pass | Boundary evidence retains source file, line, scope, safe configuration key, and function ownership where available |
| Repository-wide boundary report | Pass | `graph_engine.cli boundaries` emits bounded text or complete JSON without contacting Neo4j |
| Non-speculative cross-repository output | Pass | Automatic route candidates remain `SUGGESTED`, validated coverage remains separate, and unmatched contracts remain unresolved |
| Live-graph parity | Pass | Rebuilds retain one repository root and preserve parser counts, candidates, validation state, and observed systems consistently |
| Next.js monorepo discovery | Pass | App Router routes and same-origin requests are preserved across frontend, backend, worker, database, and infrastructure packages |
| Worker/database package discovery | Pass | Python database clients, Redis roles, Prisma providers, database-specific SQL, and Compose evidence are retained |
| Next.js handler provenance | Pass | Locally declared handlers link to functions; imported handlers remain explicit provenance gaps instead of fabricated symbols |
| Proxy-based frontend boundary | Pass | Configured Axios calls compose with proxy prefixes and retain environment-backed or literal service identities |
| Go/Gin service discovery | Pass | Nested, cross-package, and same-package helper Gin groups compose into effective runtime routes while Go outbound requests retain evidence |
| Admin frontend boundary | Pass | Named exported Axios clients resolve consistently and code-level frontend/backend contract gaps remain unresolved |
| Representative parser tests | Pass | All 85 unit tests pass, including database, cache, messaging-role, literal-host, local-port, provider, Next.js, default and named configured Axios clients, Go/Gin package and helper composition, Go HTTP requests, Prisma/SQL, Python worker, secret-redaction, false-positive, and trust-boundary fixtures |

### Exit criteria

Milestone 1 is complete only when every acceptance scenario and representative private validation scan:

- reports service boundaries with clean identities and evidence;
- reports declared database and other surrounding-system dependencies;
- detects literal external destinations supported by code;
- distinguishes Redis storage/cache behavior from messaging;
- provides a repository-wide system-boundary output;
- preserves monorepo frontend, backend, worker, database, and infrastructure boundaries;
- and never presents an automatically matched backend repository as proven runtime topology.

Later HITL validation promotes selected service-to-repository mappings into trusted topology. It must build on this milestone's evidence rather than compensate for missing or incorrect parsing.

Correctness and trust are the Milestone 1 gate. Scan-performance work remains an optimization target against the original three-second aspiration, and private benchmark results are intentionally kept outside this public repository.
