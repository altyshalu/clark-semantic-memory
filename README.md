# Clark Semantic Memory

Standalone semantic memory built around your CLARK idea, but **separate from `jessica-knowledge-graph`**.

This project stores durable knowledge about:

- projects
- tools
- rules
- stable operating context

## Design

The retrieval loop is Clark-shaped:

1. **Seeding**
   - lexical overlap
   - deterministic semantic similarity via local hash embeddings
2. **Value Iteration**
   - propagates confidence over the semantic graph
3. **A*-style frontier expansion**
   - expands from strong seeds through semantic edges
4. **Confidence reinforcement**
   - retrieved memories become slightly stronger after use

## Storage

Everything lives in one local SQLite database:

- `semantic_nodes`
- `semantic_edges`
- `retrieval_events`

## Quick start

```bash
cd /Users/altynaiakylbekova/Downloads/clark-semantic-memory
uv venv .venv
uv pip install --python .venv/bin/python3 -e .
```

## CLI

```bash
.venv/bin/clark-memory remember-project "a-zone" "A Zone prepares startups before investor matching." --key "project:a-zone:mission"
.venv/bin/clark-memory remember-tool "Hermes" "Hermes is the runtime behind Jessica." --key "tool:hermes:runtime"
.venv/bin/clark-memory remember-rule "file-safety" "Never overwrite user-owned files without approval." --key "rule:file-safety"

.venv/bin/clark-memory query "What runtime powers Jessica?"
.venv/bin/clark-memory context
.venv/bin/clark-memory list
```

## Python API

```python
from clark_semantic_memory import ClarkSemanticMemory

memory = ClarkSemanticMemory()
memory.remember_project(
    "A Zone prepares startups before investor matching.",
    project="a-zone",
    canonical_key="project:a-zone:mission",
)
memory.remember_tool(
    "Hermes is the runtime behind Jessica.",
    tool_name="Hermes",
    canonical_key="tool:hermes:runtime",
)
result = memory.query("What runtime powers Jessica?")
print(result["results"][0]["statement"])
```
