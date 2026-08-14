#!/usr/bin/env bash
set -euo pipefail

hostname="${GRAPH_TUNNEL_HOSTNAME:-${1:-}}"
local_port="${2:-7687}"

if [[ -z "$hostname" ]]; then
  printf 'Usage: %s graph-bolt.example.com [local-port]\n' "$0" >&2
  printf 'Or set GRAPH_TUNNEL_HOSTNAME in the environment.\n' >&2
  exit 2
fi

printf 'Cloudflare Bolt proxy listening at bolt://127.0.0.1:%s\n' "$local_port"
exec cloudflared access tcp --hostname "$hostname" --url "127.0.0.1:$local_port"
