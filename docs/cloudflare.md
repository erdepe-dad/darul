# Cloudflare remote access

Darul can keep Neo4j and the visualization bound to localhost while Cloudflare Tunnel publishes authenticated hostnames. Use separate hostnames for Bolt TCP and the HTTP visualization.

Example:

```text
graph-bolt.example.com  -> tcp://localhost:7687
graph.example.com       -> http://localhost:38533
```

## Security prerequisites

- Keep `GRAPH_DB_BIND_ADDRESS=127.0.0.1`.
- Keep `GRAPH_HTTP_BIND_ADDRESS=127.0.0.1`.
- Run the visualization with its default `127.0.0.1` host.
- Protect both public hostnames with Cloudflare Access policies.
- Never place a Neo4j password in tunnel configuration, shell history, source control, or browser JavaScript.

## Server tunnel

Authenticate and create a named tunnel on the server:

```bash
cloudflared tunnel login
cloudflared tunnel create darul-graph
```

Create a Cloudflare configuration file using the tunnel UUID and credential-file path printed by Cloudflare:

```yaml
tunnel: YOUR_TUNNEL_UUID
credentials-file: /home/your-user/.cloudflared/YOUR_TUNNEL_UUID.json

ingress:
  - hostname: graph-bolt.example.com
    service: tcp://localhost:7687
  - hostname: graph.example.com
    service: http://localhost:38533
  - service: http_status:404
```

Create DNS records and run the tunnel:

```bash
cloudflared tunnel route dns darul-graph graph-bolt.example.com
cloudflared tunnel route dns darul-graph graph.example.com
cloudflared tunnel run darul-graph
```

For automatic startup, install the tunnel using the service mechanism supported by your operating system and verify the exact generated unit before enabling it.

## Cloudflare Access

In Cloudflare Zero Trust:

1. Create a self-hosted Access application for `graph.example.com`.
2. Create a second application for `graph-bolt.example.com`.
3. Add an allow policy limited to your identity provider users, groups, device posture, or service tokens.
4. Deny all unmatched requests.
5. Test policy enforcement from a network that is not your LAN.

The web visualization has no built-in login. Publishing it without an Access policy reveals repository paths, symbol names, routes, and recorded decisions.

## Remote Bolt client

Install `cloudflared` on the remote client and open a local authenticated TCP listener:

```bash
scripts/connect-cloudflare.sh graph-bolt.example.com 7687
```

In a second terminal, configure Darul to use that local bridge:

```bash
export GRAPH_DB_URI=bolt://127.0.0.1:7687
export GRAPH_DB_USER=neo4j
export GRAPH_DB_PASS='read-from-your-secret-manager'
.venv/bin/python3 -m graph_engine.cli doctor
```

The native Neo4j Bolt driver does not authenticate directly through a standard Cloudflare HTTP proxy. The `cloudflared access tcp` process performs the Access login and transports Bolt through the tunnel.

## Remote visualization

Start Darul on the server without LAN publishing:

```bash
.venv/bin/python3 -m graph_engine.cli visualize --host 127.0.0.1 --port 38533
```

Visit `https://graph.example.com/`. Cloudflare Access should authenticate the browser before proxying the request to Darul.

## Troubleshooting

```bash
cloudflared tunnel info darul-graph
cloudflared tunnel ingress validate
curl -I http://127.0.0.1:38533/
.venv/bin/python3 -m graph_engine.cli doctor
```

- A `502` usually means the tunnel cannot reach the local service.
- An Access login loop usually indicates an application-domain or identity-policy mismatch.
- A Bolt timeout often means the client-side TCP bridge is not running or is listening on a different port.
- A Neo4j authentication error means the tunnel is working but the database credentials are incorrect.
