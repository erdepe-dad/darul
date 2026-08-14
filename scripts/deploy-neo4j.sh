#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$repo_root/.graph_engine/neo4j.env"

if [[ ! -f "$env_file" ]]; then
  umask 077
  graph_password="$(openssl rand -hex 32)"
  printf 'GRAPH_DB_URI=bolt://127.0.0.1:7687\nGRAPH_DB_BIND_ADDRESS=127.0.0.1\nGRAPH_HTTP_BIND_ADDRESS=127.0.0.1\nGRAPH_DB_USER=neo4j\nGRAPH_DB_PASS=%s\nNEO4J_AUTH=neo4j/%s\n' \
    "$graph_password" "$graph_password" > "$env_file"
  printf 'Created %s with mode 0600. Edit GRAPH_DB_URI and GRAPH_DB_BIND_ADDRESS for LAN access.\n' "$env_file"
fi

docker compose --env-file "$env_file" -f "$repo_root/compose.yaml" up -d
