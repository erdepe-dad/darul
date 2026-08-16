# LLM Context Benchmark

This benchmark measures Darul's primary claim: a coding agent can answer a cross-project codebase question from a compact Neo4j subgraph instead of repeatedly searching and reading source files.

The baseline is intentionally retained so future parser improvements can be compared against the same workload. Append new results to the revision history rather than replacing the original measurement.

## Baseline

| Field | Value |
| --- | --- |
| Date | 2026-08-15 UTC |
| Darul commit | `f6ed465` |
| Workload | Multi-repository Java/Vaadin behavioral trace |
| Seed view | `ExampleTaskView` |
| Graph size returned | 63 nodes, 83 links, 14 alternatives |
| Codex CLI | `0.147.0` |
| Model | `cx/gpt-5.6-sol` through the locally configured provider |
| Reasoning effort | Medium |
| Answer constraint | At most 350 words |
| Final answer length | 225 words in both runs |

The identical question in both runs was:

> Explain the end-to-end behavior of `ExampleTaskView`: user actions, service calls, HTTP endpoints/backend routes, and unresolved edges. Cite class and method names.

## Test Modes

### Darul context

1. Query Neo4j once with `graph_engine.cli trace`.
2. Render the result as the compact Mermaid behavioral projection.
3. Give that projection to the model as its only codebase context.
4. Prohibit filesystem inspection and other tools.

The Neo4j trace itself completed in 0.43 seconds. The model issued no file-search or file-reading commands.

### Conventional inspection

1. Start the same model in the source repository with a read-only sandbox.
2. Prohibit Darul, Neo4j, graph artifacts, and graph or memory tools.
3. Allow normal filesystem searches and file reads across the two repositories.
4. Ask the identical question with the same answer-length constraint.

The model issued seven shell tool calls containing `rg`, `find`, `sed`, and numbered source reads. Those calls returned 127,503 characters of tool output.

## Measured Results

Token counts come directly from the `turn.completed.usage` object emitted by `codex exec --json`.

| Metric | Darul and Neo4j | Source inspection | Source / Darul |
| --- | ---: | ---: | ---: |
| Input tokens | 13,723 | 208,463 | 15.19x |
| Cached input tokens | 0 | 157,184 | n/a |
| Uncached input tokens | 13,723 | 51,279 | 3.74x |
| Output tokens | 685 | 2,384 | 3.48x |
| Reasoning output tokens | 80 | 612 | 7.65x |
| Total model tokens | 14,408 | 210,847 | 14.63x |
| Model-side file tool calls | 0 | 7 | n/a |
| Tool output characters | 0 | 127,503 | n/a |
| Approximate end-to-end runtime | 15 seconds | 87 seconds | 5.8x |

`Total model tokens` is `input_tokens + output_tokens`. Reasoning tokens are reported separately and are not added again. `Uncached input tokens` is `input_tokens - cached_input_tokens`; cached tokens may still have provider-specific cost.

Compared with conventional inspection, the Darul run used:

- 93.4% fewer total input tokens.
- 73.2% fewer uncached input tokens despite the source run's 75.4% cache hit.
- 71.3% fewer output tokens across reasoning, tool selection, and the final answer.
- 93.2% fewer total model tokens.

## Context Payload

The output format materially affects the result. The compact behavioral projection should be sent to the model, not the complete trace JSON.

| Payload | Characters | Approximate tokens at four characters per token |
| --- | ---: | ---: |
| Relevant full source files | 178,871 | 44,718 |
| Optimistic targeted source snippets | 29,314 | 7,328 |
| Raw Darul trace JSON | 108,609 | 27,152 |
| Compact Darul Mermaid trace | 5,462 | 1,366 |

The compact projection is 32.7 times smaller than the relevant full files and 5.4 times smaller than an optimistic targeted-read payload. These payload figures are estimates; the measured Codex usage above is authoritative for this run.

## Answer Quality

The Darul answer correctly recovered the main UI actions, service calls, external requests, cross-repository backend routes, and unresolved route evidence.

The conventional source inspection recovered additional behavior that was missing or incomplete in the graph:

- The downstream submit and detach lifecycle.
- Additional persistence and history endpoints.
- Complete status-transition and deletion-condition alternatives.
- UI state assignments performed by grid selection.
- Operational risks such as repeated listener registration and swallowed failures.

The baseline therefore demonstrates a large efficiency gain, but not yet full behavioral parity. Darul is already suitable for orientation and targeted retrieval; deep debugging still benefits from source verification when the graph reports an evidence gap.

## Parser Improvement Targets

Before rerunning the benchmark, improve extraction and stitching for:

- Assignments and component-state changes inside UI event handlers.
- Calls made by downstream views and components reached from the seed view.
- Generic persistence endpoints that do not use the currently recognized API patterns.
- Complete multiline boolean conditions and all status alternatives.
- Failure handling and lifecycle effects that are structurally visible in catch blocks or repeated listener registration.

## Reproduction

The benchmark requires two repositories already ingested into the same Neo4j database and a working Codex CLI login. Keep credentials in the ignored graph environment file; do not place them in benchmark artifacts.

Generate the compact graph context:

```bash
export CURRENT_REPO_NAME="repository-a"
export BENCHMARK_VIEW="ExampleTaskView"
export BENCHMARK_MODEL="your-model-id"

.venv/bin/python3 -m graph_engine.cli trace \
  --view "$BENCHMARK_VIEW" \
  --path-limit 1200 \
  --format mermaid > /tmp/darul-benchmark-trace.mmd
```

Run the graph-context model pass:

```bash
codex exec --json --ephemeral --sandbox read-only \
  --skip-git-repo-check -C /tmp \
  -m "$BENCHMARK_MODEL" -c 'model_reasoning_effort="medium"' \
  "Use only the supplied Darul graph trace. Do not call tools or inspect files. Explain the end-to-end behavior of $BENCHMARK_VIEW: user actions, service calls, HTTP endpoints/backend routes, and unresolved edges. Keep the answer under 350 words and cite class and method names." \
  < /tmp/darul-benchmark-trace.mmd \
  > /tmp/darul-benchmark-graph.jsonl
```

Run the conventional source pass from the primary source repository:

```bash
codex exec --json --ephemeral --sandbox read-only \
  -C /path/to/repository-a \
  -m "$BENCHMARK_MODEL" -c 'model_reasoning_effort="medium"' \
  "Do not use Darul, Neo4j, graph or memory tools, or precomputed graph artifacts. Inspect /path/to/repository-a and /path/to/repository-b only through normal filesystem search and file-reading tools. Explain the end-to-end behavior of $BENCHMARK_VIEW: user actions, service calls, HTTP endpoints/backend routes, and unresolved edges. Keep the answer under 350 words and cite class and method names." \
  > /tmp/darul-benchmark-source.jsonl
```

Extract exact usage records:

```bash
rg '"type":"turn.completed"' \
  /tmp/darul-benchmark-graph.jsonl \
  /tmp/darul-benchmark-source.jsonl
```

The JSON usage format is documented in the official OpenAI Codex non-interactive mode documentation: <https://learn.chatgpt.com/docs/non-interactive-mode>.

## Update Protocol

Keep the workload, model, reasoning effort, prompt, sandbox, answer constraint, and repository revisions fixed when measuring parser changes. Record any unavoidable difference.

For each rerun:

1. Record the Darul commit and fixture repository commits.
2. Rebuild both repository graphs from a clean structural state.
3. Run each mode once, or run both modes repeatedly and report medians.
4. Save the JSONL usage records outside version control.
5. Compare token usage, runtime, tool calls, and answer coverage.
6. Check each parser improvement target explicitly.
7. Append a revision-history row; do not overwrite the baseline.

## Revision History

| Date | Darul commit | Darul total tokens | Source total tokens | Reduction | Quality status |
| --- | --- | ---: | ---: | ---: | --- |
| 2026-08-15 | `f6ed465` | 14,408 | 210,847 | 14.63x | Efficient orientation; known parser coverage gaps |
