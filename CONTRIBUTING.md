# Contributing

Thank you for improving Darul. Contributions can include parser support, framework extraction, graph queries, visualization work, documentation, tests, and reproducible bug reports.

## Before opening an issue

- Search existing issues and discussions.
- Remove credentials, private hostnames, proprietary source, prompts, and database exports from examples.
- For security vulnerabilities, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.
- Include the Python, Neo4j, operating-system, and Docker versions relevant to the problem.

## Development setup

```bash
git clone https://github.com/erdepe-dad/darul.git
cd darul
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Tests do not require a live database.
.venv/bin/python3 -m unittest discover -s tests -v
```

Use `scripts/deploy-neo4j.sh` only when integration behavior needs a live Neo4j instance. The generated credential file is local and ignored.

## Change workflow

1. Create a focused branch from the current default branch.
2. Add or update tests before changing behavior.
3. Keep changes narrow enough to review independently.
4. Run the complete validation set.
5. Run the public-release audit before committing.
6. Open a pull request describing behavior, risks, and verification.

```bash
.venv/bin/python3 -m unittest discover -s tests -v
.venv/bin/python3 -m compileall -q .graph_engine graph_engine tests
node --check .graph_engine/web/app.js
scripts/public-release-audit.py
```

## Parser contributions

- Parsing must remain local and deterministic.
- Do not add network calls, telemetry, or model inference to source scanning.
- Preserve repository-scoped identity formats.
- Treat malformed or unsupported source as a reported parse error, not a repository-wide crash.
- Add fixtures for valid extraction, false-positive prevention, and syntax variants.
- Document whether support uses a full AST, tree-sitter, or a bounded fallback parser.
- Avoid broad regexes that treat test mocks or unrelated annotations as routes.

## Database contributions

- Use parameterized Cypher for all user-derived values.
- Preserve other repositories during builds and synchronization.
- Keep visualization queries read-only.
- Include a migration note for schema or identity changes.
- Do not assume APOC is installed unless the feature explicitly declares it as an optional dependency.

## Frontend contributions

- Preserve the Folded Neighborhood visual language documented in [DESIGN.md](DESIGN.md).
- Keep the graph usable with mouse, touch, keyboard, and assistive technology.
- Test desktop and mobile layouts.
- Do not expose credentials or arbitrary Cypher execution to the browser.
- Keep the frontend zero-build unless maintainers approve a toolchain change.

## Documentation contributions

Commands must be runnable, use placeholders rather than real infrastructure, and state security implications for LAN or remote exposure. Avoid screenshots containing private repository names, paths, browser profiles, or host details.

## Commit and pull-request expectations

- Use clear imperative commit subjects.
- Explain user-visible behavior and compatibility impact.
- Link relevant issues.
- State the tests you ran.
- Keep generated caches, local environment files, database dumps, agent prompts, and design-review artifacts out of commits.

Contributions are accepted under the repository's [MPL-2.0 license](LICENSE). By submitting a contribution, you represent that you have the right to license it under those terms.

## Community

Follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Review is technical and direct, but it must remain respectful and focused on the work.
