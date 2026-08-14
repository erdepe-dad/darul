#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$repo_root/.graph_engine/neo4j.env"

if [[ ! -f "$env_file" ]]; then
  printf 'Credential file not found: %s\n' "$env_file" >&2
  exit 2
fi

graph_user="neo4j"
old_password=""
while IFS='=' read -r key value; do
  case "$key" in
    GRAPH_DB_USER) graph_user="$value" ;;
    GRAPH_DB_PASS) old_password="$value" ;;
  esac
done < "$env_file"

if [[ -z "$old_password" ]]; then
  printf 'GRAPH_DB_PASS is missing from %s\n' "$env_file" >&2
  exit 2
fi

container_ref="${1:-}"
if [[ -z "$container_ref" ]]; then
  container_ref="$(docker compose --env-file "$env_file" -f "$repo_root/compose.yaml" ps -q neo4j)"
fi
if [[ -z "$container_ref" ]]; then
  printf 'Neo4j container is not running. Start it before rotating the password.\n' >&2
  exit 2
fi

new_password="$(openssl rand -hex 32)"
docker exec "$container_ref" cypher-shell -u "$graph_user" -p "$old_password" \
  "ALTER CURRENT USER SET PASSWORD FROM '$old_password' TO '$new_password'"

umask 077
temp_file="$(mktemp "$env_file.tmp.XXXXXX")"
trap 'rm -f "$temp_file"' EXIT
while IFS= read -r line || [[ -n "$line" ]]; do
  case "$line" in
    GRAPH_DB_PASS=*) printf 'GRAPH_DB_PASS=%s\n' "$new_password" ;;
    NEO4J_AUTH=*) printf 'NEO4J_AUTH=%s/%s\n' "$graph_user" "$new_password" ;;
    *) printf '%s\n' "$line" ;;
  esac
done < "$env_file" > "$temp_file"
chmod 600 "$temp_file"
mv "$temp_file" "$env_file"
trap - EXIT

docker compose --env-file "$env_file" -f "$repo_root/compose.yaml" up -d --force-recreate neo4j
printf 'Neo4j password rotated and the container recreated with the persistent data volume attached.\n'
