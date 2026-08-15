# Darul Graph Engine

[![CI](https://github.com/erdepe-dad/darul/actions/workflows/ci.yml/badge.svg)](https://github.com/erdepe-dad/darul/actions/workflows/ci.yml)
[![License: MPL-2.0](https://img.shields.io/badge/License-MPL--2.0-brightgreen.svg)](LICENSE)

Darul is a local-first structural code knowledge graph. It parses repositories without language-model tokens, stores globally scoped code entities and engineering decisions in Neo4j, stitches frontend requests to backend routes, and retrieves small relevant subgraphs for developers and coding agents.

The repository includes a read-only, responsive graph atlas for human exploration.

## Why Darul

- Parse source structure locally without sending source code to an AI service.
- Keep multiple repositories in one graph without ID collisions.
- Connect pages, API calls, backend routes, handlers, files, symbols, sessions, and decisions.
- Update only changed file subtrees after Git merges.
- Retrieve targeted decision and code neighborhoods instead of repeatedly reading whole repositories.
- Inspect the graph through a polished browser interface without exposing database credentials to JavaScript.

## Supported source inspection

| Language / framework | Extracted structure |
| --- | --- |
| Python | Imports, classes, functions, async functions, HTTP calls, common backend routes |
| JavaScript / TypeScript | Imports, functions, classes, `fetch`, Axios, Express-style routes |
| Go | Imports, functions, and common HTTP router methods |
| Java | Imports, classes, interfaces, records, enums, methods, and overload-aware IDs |
| Spring Boot | Controller mappings, Feign mappings, RestTemplate/WebClient calls, and CRNK JSON:API resources |
| Vaadin | Flow `@Route` pages and Vaadin 8 `@SpringView` pages |
| Flowable | BPMN processes, workflow steps, sequence flows, call activities, delegate bindings, runtime starts, and `JavaDelegate` implementations |
| Messaging | Kafka, RabbitMQ, Redis, JMS, and Spring Cloud Stream publishers/listeners; RabbitMQ routing-key-to-queue bindings |

Neo4j 5.20 or newer is the supported database. Other Bolt-compatible graph databases may require schema-query adaptations.

## Requirements

- Linux, macOS, or WSL
- Python 3.11 or newer
- Git
- Docker with Docker Compose for the included Neo4j deployment
- OpenSSL for automatic password generation
- Optional: `cloudflared` for authenticated remote access

## Quick start

```bash
git clone https://github.com/erdepe-dad/darul.git
cd darul

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Generates a random password in an ignored mode-0600 file and starts Neo4j.
scripts/deploy-neo4j.sh

# Verify configuration and database connectivity.
.venv/bin/python3 -m graph_engine.cli doctor

# Parse and ingest this repository.
.venv/bin/python3 -m graph_engine.cli build

# Open the read-only atlas locally.
.venv/bin/python3 -m graph_engine.cli visualize --open
```

The default visualization address is `http://127.0.0.1:38533/`.

## Configuration

Darul reads process environment variables first, then `.env`, then `.graph_engine/neo4j.env`. Existing process variables are never overwritten.

| Variable | Default | Purpose |
| --- | --- | --- |
| `GRAPH_DB_URI` | `bolt://127.0.0.1:7687` | Neo4j Bolt connection URI |
| `GRAPH_DB_USER` | `neo4j` | Database username |
| `GRAPH_DB_PASS` | none | Required database password |
| `GRAPH_DB_DATABASE` | driver default | Optional Neo4j database name |
| `GRAPH_DB_TIMEOUT` | `3` | Connection timeout in seconds |
| `GRAPH_DB_BIND_ADDRESS` | `127.0.0.1` | Docker Bolt publishing address |
| `GRAPH_HTTP_BIND_ADDRESS` | `127.0.0.1` | Docker Neo4j Browser publishing address |
| `CURRENT_REPO_NAME` | Git root name | Explicit repository graph namespace |
| `GRAPH_ENGINE_EXCLUDES` | none | Additional comma-separated directory names to skip |
| `GRAPH_ENGINE_WORKERS` | up to 4 CPUs | Parser process count; set to `1` for sequential scans |
| `GRAPH_ENGINE_JSON_API_PREFIXES` | discovered from properties | Optional comma-separated CRNK/Katharsis server prefixes when configuration lives outside the repository |
| `GRAPH_TUNNEL_HOSTNAME` | none | Cloudflare Access TCP hostname used by the helper script |
| `DARUL_OPERATOR_TOKEN` | none | Bearer token required for build, sync, and service-mapping HTTP mutations |
| `DARUL_OPERATOR_TOKEN_FILE` | none | Read the operator Bearer token from a protected file when the direct variable is unset |
| `DARUL_OPERATOR_ORIGINS` | local ports 4173/5173 | Exact comma-separated browser origins allowed to call the operator API |
| `DARUL_REPO_PARENT` | parent of Darul | Parent directory used to resolve bounded repository names for operator actions |
| `DARUL_REPO_ROOTS` | none | Optional `name=/path` comma-separated overrides for operable repositories |
| `DARUL_OPERATOR_WORKERS` | `1` | Background build/sync concurrency, bounded to two workers |
| `DARUL_OPERATOR_TIMEOUT` | `900` | Maximum seconds for one background build or sync operation |

For a manual setup, copy the safe template and replace both password placeholders with the same random value:

```bash
cp .graph_engine/neo4j.env.example .graph_engine/neo4j.env
chmod 600 .graph_engine/neo4j.env
```

Never commit `.env` or `.graph_engine/neo4j.env`. Both are ignored, and `scripts/public-release-audit.py` checks the public file set for likely credentials.

Rotate a local generated password without printing it:

```bash
scripts/rotate-neo4j-password.sh
```

## Commands

```bash
# Parse locally without contacting Neo4j.
.venv/bin/python3 -m graph_engine.cli build --dry-run

# Create constraints, replace this repository's structural subtree, and stitch APIs.
.venv/bin/python3 -m graph_engine.cli build

# Apply an incremental Git diff. Defaults to ORIG_HEAD..HEAD.
.venv/bin/python3 -m graph_engine.cli sync
.venv/bin/python3 -m graph_engine.cli sync --base HEAD~1 --head HEAD

# Inspect the end-to-end neighborhood for a page or component.
.venv/bin/python3 -m graph_engine.cli inspect --page src/pages/checkout.tsx

# Trace a Vaadin view or Java entry point through calls, HTTP, messaging, and Flowable.

# Persist runtime service URLs used to resolve configuration-key-based requests.
.venv/bin/python3 -m graph_engine.cli services set \
  --base-url http://127.0.0.1:10000/api --target-repo admin-rest

# Record an architectural decision and optionally supersede an earlier decision.
.venv/bin/python3 -m graph_engine.cli decision \
  --title "Use Redis for sessions" \
  --rationale "Lower request latency" \
  --file src/session.py

# Install an idempotent post-merge sync hook without replacing existing hook logic.
.venv/bin/python3 -m graph_engine.cli install-hooks

# Start the visualization on localhost.
.venv/bin/python3 -m graph_engine.cli visualize

# Publish the visualization to a trusted LAN.
.venv/bin/python3 -m graph_engine.cli visualize --host 0.0.0.0 --port 38533
```

Python cannot execute a leading-dot top-level package with `python -m`. The small `graph_engine/` package is therefore an import bridge to the implementation in `.graph_engine/`.

## Using one database across projects

Install Darul in each repository or invoke it with that repository as the working directory. Entity IDs are scoped using the repository name:

```text
repository:path/to/file.py
repository:path/to/file.py::ClassName
repository:path/to/file.py::function_name
repository:GET:/api/users/{param}
```

If two repositories have the same directory name, set a stable unique namespace before building:

```bash
export CURRENT_REPO_NAME=checkout-service
.venv/bin/python3 -m graph_engine.cli build
```

The full build replaces structural data only for the active repository. Other repository roots remain untouched.

## Visualization

The Folded Neighborhood atlas supports:

- Repository, node-type, and relationship filtering
- Search and configurable graph density
- Directional relationships and selected-edge labels
- Workflow process lanes with task ordering, called processes, and resolved Java delegates
- View-centric evidence sheets spanning UI actions, Java calls, HTTP routes, message channels, workers, Flowable branches, and external systems
- Folded-lane and free-force layouts
- Two-hop neighborhood folding with preserved navigation history
- Node properties and immediate-neighbor inspection
- Mouse, touch, and keyboard navigation
- Responsive scope and inspector drawers
- A semantic node list for assistive technology

Database credentials remain in the Python process. The browser receives only read-only JSON graph results.

Binding the server to `0.0.0.0` exposes repository metadata to the network without application-level authentication. Use localhost by default and put Cloudflare Access, a trusted reverse proxy, or an equivalent authentication layer in front of remote deployments.


### Connecting Flowable and worker services

Darul creates a Flowable path only when source evidence joins all three parts: a Java method starts a process key, an ingested BPMN `<process id>` has the same key, and BPMN delegate or expression bindings resolve to Java classes. Build every repository that owns those parts into the same Neo4j database. If an HTTP call targets a separately deployed service, scan that service too; Darul does not invent a process link across an unresolved external endpoint.

Asynchronous boundaries use `MessageChannel` nodes instead of HTTP routes. A producer method connects with `PUBLISHES_TO`; RabbitMQ bindings use `ROUTES_TO`; matching channels across repositories use `SAME_CHANNEL`; and a listener connects to its worker method with `CONSUMED_BY`. Supported static patterns include `KafkaTemplate`, `@KafkaListener`, `RabbitTemplate`, `@RabbitListener`, Redis/JMS templates and listeners, and Spring Cloud Stream listeners. Dynamic topic names without a literal, constant, or Spring-property default remain unresolved and are shown as an evidence gap.

## Docker persistence and restart behavior

The included Compose service uses named volumes:

- `darul-neo4j-data` mounted at `/data`
- `darul-neo4j-logs` mounted at `/logs`

The restart policy is `unless-stopped`, so Neo4j starts again when Docker starts unless an administrator explicitly stopped it. Removing or recreating the container does not remove graph data. Removing the named data volume does.

Inspect the deployment without printing credentials:

```bash
docker compose --env-file .graph_engine/neo4j.env ps
set -a
source .graph_engine/neo4j.env
set +a
docker compose --env-file .graph_engine/neo4j.env exec neo4j cypher-shell \
  -u neo4j -p "$GRAPH_DB_PASS" "RETURN 1"
unset GRAPH_DB_PASS NEO4J_AUTH
```

Before upgrades or destructive maintenance, create a Neo4j database dump or a storage-level snapshot of the named data volume. Follow the backup procedure for your exact Neo4j edition and version; do not treat container persistence as a backup.

## Remote access with Cloudflare

Keep Bolt bound to `127.0.0.1` on the server and expose it through Cloudflare Tunnel plus an Access policy. Remote clients run a local authenticated TCP bridge and connect Darul to that local port.

```bash
scripts/connect-cloudflare.sh graph-bolt.example.com 7687

export GRAPH_DB_URI=bolt://127.0.0.1:7687
export GRAPH_DB_PASS='your-local-secret'
.venv/bin/python3 -m graph_engine.cli doctor
```

See [docs/cloudflare.md](docs/cloudflare.md) for server tunnel configuration, DNS, Access policy, Bolt clients, and protecting the web visualization.

## Coding-agent hooks

Darul includes two standard-input hook programs:

```bash
.venv/bin/python3 -m graph_engine.hooks.context_inject
.venv/bin/python3 -m graph_engine.hooks.event_logger
```

- `context_inject` reads a prompt payload and prints a delineated block containing active decisions and prompt-relevant hotspots.
- `event_logger` records session events and file targets. Event payloads may contain prompts, paths, and tool data; enable it only when that information is appropriate to retain in your Neo4j database.

See [docs/hooks.md](docs/hooks.md) for example lifecycle-hook configuration and privacy controls.

## Architecture

The implementation is split into configuration, Bolt access, parsers, ingestion, endpoint stitching, Git synchronization, hooks, and visualization modules. See [docs/architecture.md](docs/architecture.md) for the schema, data flow, extension points, and current parser limitations.

The reproducible [LLM context benchmark](docs/benchmark.md) records the baseline token, runtime, tool-call, and answer-quality comparison between Darul retrieval and conventional source inspection.

## Development

```bash
.venv/bin/python3 -m unittest discover -s tests -v
.venv/bin/python3 -m compileall -q .graph_engine graph_engine tests
node --check .graph_engine/web/app.js
scripts/public-release-audit.py
```

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before submitting changes. Project credits are listed in [CONTRIBUTORS.md](CONTRIBUTORS.md).

## Security model

- Parsing is local and deterministic; source files are not sent to an external model.
- Neo4j credentials are loaded server-side and are not exposed by the visualization API.
- The visualization is read-only but has no built-in user authentication.
- The event logger intentionally stores hook payloads and must be treated as potentially sensitive.
- LAN Bolt publishing and remote tunnels must be protected by firewall and identity policy.

Please report vulnerabilities privately using the process in [SECURITY.md](SECURITY.md).

## License

Darul is available under the [Mozilla Public License 2.0](LICENSE). You may use it commercially and combine it with proprietary software. Changes to MPL-covered source files must remain available under the MPL when distributed.

Third-party assets and their licenses are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
