# Graphiti Retrieval Evaluation

This directory is an isolated research harness. It does not add Graphiti to
Marven's runtime, replace canonical SQLite memory, or allow Graphiti to write
back extracted facts.

The harness targets `graphiti-core==0.30.2`, requires Python 3.10 or newer,
and uses a separate Neo4j database. It defaults to a loopback
OpenAI-compatible model endpoint, disables Graphiti telemetry, disables raw
episode storage, and creates a fresh non-identifying Graphiti group for every
distinct captured agent/session and policy-filter scope in a run.

## Prerequisites

1. Collect reviewed labels with `scripts/label_memory_retrieval.py`.
2. Export them with `scripts/export_retrieval_labels.py`.
3. Create a separate Python 3.10+ virtual environment and install this
   directory's pinned requirements.
4. Run an isolated Neo4j 5.26 instance.
5. Run a local OpenAI-compatible LLM and embedding endpoint that reliably
   returns Graphiti's required structured output.

Example PowerShell flow:

```powershell
python scripts/label_memory_retrieval.py `
  --workspace heshware `
  --owner akeem

python scripts/export_retrieval_labels.py `
  reports/retrieval-labels.json `
  --workspace heshware `
  --owner akeem

py -3.11 -m venv .venv-graphiti
.\.venv-graphiti\Scripts\Activate.ps1
python -m pip install -r experiments/graphiti/requirements.txt

$env:NEO4J_URI = "bolt://localhost:7687"
$env:NEO4J_USER = "neo4j"
$env:NEO4J_PASSWORD = "use-a-local-secret"
$env:GRAPHITI_LLM_BASE_URL = "http://localhost:11434/v1"
$env:GRAPHITI_LLM_MODEL = "deepseek-r1:7b"
$env:GRAPHITI_EMBEDDING_MODEL = "nomic-embed-text"
$env:GRAPHITI_EMBEDDING_DIMENSION = "768"
$env:GRAPHITI_STRUCTURED_OUTPUT_MODE = "json_schema"

python experiments/graphiti/evaluate_graphiti.py `
  reports/retrieval-labels.json `
  --root . `
  --output reports/graphiti-evaluation.json
```

The example model names follow Graphiti's current local-provider documentation;
they are not endorsed Marven production defaults. Local structured-output
reliability must be measured because ingestion can fail or create poor graph
facts when the model does not honor the requested schema.
If a compatible local endpoint accepts `json_schema` but does not enforce it,
try `GRAPHITI_STRUCTURED_OUTPUT_MODE=json_object` and record that configuration
with the result rather than silently changing modes.

## Decision rule

The report will not recommend continued evaluation until it has at least 50
labeled queries with 80% average coverage. After that, Graphiti must improve
NDCG at the largest requested cutoff by at least 0.05 without reducing recall
by more than 0.02. Any unmapped episode reference fails the scope/provenance
gate. Deletion propagation, provider cost, ingestion failures, and adversarial
scope isolation still require separate tests before any runtime proposal.

See [`docs/GRAPHITI_EVALUATION.md`](../../docs/GRAPHITI_EVALUATION.md) for the
architecture decision and non-negotiable governance boundaries.
