# Changelog

All notable changes to Darul are documented here.

## Unreleased

### Added

- Evidence-true `boundaries` CLI report for repository-wide HTTP services and surrounding systems
- Source/configuration detection for databases, caches, messaging, workflow, storage, search, email, identity, communications, payment gateways, and literal HTTP host-and-port boundaries
- File-owned `USES_SYSTEM` graph evidence with technology, role, scope, line, and safe configuration-key provenance

### Changed

- Automatic endpoint matches are marked `SUGGESTED` and no longer count as resolved topology
- Default coding-agent context includes backend route connections only when they are `VALIDATED`
- Operator metrics expose candidate counts separately from validated coverage
- Dynamic Java receiver names are no longer promoted to service identities
- Split SMTP host and port properties are combined into one safe observed destination identity

## 0.1.0 - 2026-08-14

### Added

- Multi-repository Neo4j schema with globally scoped file and symbol IDs
- Local parsers for Python, JavaScript, TypeScript, Go, and Java
- Spring Boot, Vaadin, and Flowable structural inspection
- Frontend request and backend route normalization and stitching
- Incremental Git synchronization and idempotent post-merge hook installation
- Session event logging, architectural decisions, and supersession lineage
- Prompt-relevant context retrieval
- Read-only Folded Neighborhood graph visualization
- Persistent Neo4j Docker deployment with safe localhost defaults
- Cloudflare Access TCP helper and remote deployment documentation
