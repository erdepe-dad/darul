# Coding-agent hooks

Darul's hooks communicate through JSON on standard input and plain text or IDs on standard output. They can be integrated with any tool that can invoke a command during prompt and tool lifecycles.

## Context injection

`context_inject` reads prompt text from `prompt`, `user_prompt`, or `message`, queries active decisions and matching file hotspots, and prints a bounded Markdown block.

```bash
printf '%s' '{"prompt":"change checkout session handling"}' | \
  .venv/bin/python3 -m graph_engine.hooks.context_inject
```

The command exits successfully and emits an unavailable marker when Neo4j cannot be reached, so a graph outage does not block the coding session.

## Event logging

`event_logger` records the complete JSON payload, event type, session identity, timestamp, and recognized file paths.

```bash
printf '%s' '{"session_id":"demo","event":"PostToolUse","file_path":"src/app.py"}' | \
  .venv/bin/python3 -m graph_engine.hooks.event_logger
```

Important privacy behavior:

- Hook payloads can contain prompts, command arguments, paths, tool results, or other sensitive text.
- Payloads are truncated to 100,000 characters but are not semantically redacted.
- Data remains in Neo4j until the operator deletes it.
- Do not enable event logging for secrets, regulated data, or repositories whose policy forbids prompt retention.

If only architectural decisions are needed, leave general event logging disabled and use the explicit `decision` CLI command.

## Claude Code example

Use absolute paths because hooks may run with a different working directory. Adjust `/path/to/darul` and the project directory for your installation.

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "cd /path/to/project && /path/to/darul/.venv/bin/python3 -m graph_engine.hooks.context_inject"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "cd /path/to/project && /path/to/darul/.venv/bin/python3 -m graph_engine.hooks.event_logger"
          }
        ]
      }
    ]
  }
}
```

Consult the lifecycle-hook documentation for your installed agent version before editing its settings; hook schemas can change independently of Darul.

## Decision lineage

Record a decision:

```bash
.venv/bin/python3 -m graph_engine.cli decision \
  --title "Use Redis for sessions" \
  --rationale "Lower latency and shared session state" \
  --file src/session.py \
  --session architecture-review
```

Supersede it later using the returned decision ID:

```bash
.venv/bin/python3 -m graph_engine.cli decision \
  --title "Use database-backed sessions" \
  --rationale "Simpler recovery requirements" \
  --file src/session.py \
  --supersedes repository-name:DECISION_UUID
```

The old decision remains queryable with status `SUPERSEDED`; the new decision is linked to it and becomes active context.
