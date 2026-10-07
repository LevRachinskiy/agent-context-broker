# Agent Context Broker

Infrastructure for managing context, memory, tools, and observability across long-running AI agents.

A debugging agent can spend hours discovering useful facts, then repeatedly resend the same history or lose the one detail it needs. Agent Context Broker stores that history durably and turns it into an inspectable, budgeted context package. Tools and model requests pass through the same service, so their outcomes become part of the session's durable record.

**Python 3.12 · FastAPI · PostgreSQL · Redis · MCP · Prometheus**

[Architecture](docs/architecture.md) · [Measured benchmarks](docs/benchmarks.md) · [Evaluation](docs/evaluation.md) · [Interview guide](docs/interview-guide.md)

## How it fits

```mermaid
flowchart TD
    A[Agent client] --> B[REST API]
    A --> M[MCP stdio adapter]
    M --> B
    B --> C[Context pipeline]
    B --> T[Validated tool executor]
    B --> R[Model router]
    C --> D[(PostgreSQL events and memories)]
    C --> E[(Redis context cache)]
    T --> D
    R --> C
    R --> P[Model providers]
    R --> D
    B --> O[Metrics and structured logs]
```

The default profile uses **SQLite + a bounded local cache**, so the complete demo needs no cloud credentials. Docker Compose switches the same repository and cache boundaries to PostgreSQL and Redis. MCP uses the official Python SDK and exposes tools through a stdio adapter that calls the REST service.

## Implemented

- Durable sessions, typed event history, memories, and pagination.
- Pluggable embedding protocol; deterministic offline lexical vectors and cosine retrieval.
- Inspectable ranking using relevance, recency, importance, and source weights.
- Exact/near duplicate removal, budget-aware UTF-8 truncation, and before/after token estimates.
- Revision-keyed cache entries with Redis failure treated as a cache miss.
- Database-enforced event/memory idempotency with payload conflict detection.
- Trusted tool registration, JSON Schema discovery/validation, timeout handling, read-only transient retries, and persisted execution traces.
- Four safe demonstration tools: calculator, text search, memory lookup, and system status.
- Official MCP handshake, discovery, and execution tested through a real subprocess.
- Mock and OpenAI-compatible model providers; preference, latency, cost, task eligibility, and ordered fallback policies.
- Prometheus metrics, request IDs, tool trace IDs, structured logs, and persisted model outcomes.
- Labeled synthetic evaluation, reproducible loopback HTTP load tests, and CI with real-service and Compose jobs.

## Quick start

```bash
git clone https://github.com/LevRachinskiy/agent-context-broker.git
cd agent-context-broker
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

For a locked environment, install `uv` and run `uv sync --frozen --extra dev` instead of the venv/pip steps. Use `uv run --frozen --extra dev` before subsequent commands.

In another terminal, activate the same environment:

```bash
python scripts/demo.py
python scripts/evaluate.py --output docs/evaluation-results.json
python scripts/benchmark.py --concurrency 10 --flows 100 --output docs/benchmark-local.json
pytest --cov=app --cov-report=term-missing
ruff check .
ruff format --check .
mypy app
```

OpenAPI: http://127.0.0.1:8000/docs. Metrics: http://127.0.0.1:8000/metrics.

## PostgreSQL + Redis

```bash
docker compose up --build -d --wait
python scripts/demo.py
python scripts/evaluate.py
docker compose logs broker
docker compose down
```

Compose binds only to localhost. PostgreSQL is persisted in a named volume; Redis is disposable. `docker compose down -v` also deletes the database, so reserve it for disposable test environments.

Real-service tests can be run independently:

```bash
TEST_DATABASE_URL='postgresql+asyncpg://broker:broker@localhost:5432/broker' \
TEST_REDIS_URL='redis://localhost:6379/0' \
pytest tests/test_services_integration.py -q
```

The Compose services do not publish database ports. Use your own local services or the CI service containers for the command above.

## Example API calls

Create a session and use its returned `session_id`:

```bash
curl -s http://127.0.0.1:8000/sessions \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"debugger","token_budget":256}'

export SESSION_ID='<returned-session-id>'

curl -s "http://127.0.0.1:8000/sessions/$SESSION_ID/memory" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: architecture-v1' \
  -d '{"content":"Redis cache keys must include session revision.","metadata":{"key":"architecture"}}'

curl -s http://127.0.0.1:8000/context/build \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SESSION_ID\",\"query\":\"Why is Redis context stale?\"}"
```

A context item includes its scoring explanation:

```json
{
  "source": "memory",
  "content": "Redis cache keys must include session revision.",
  "semantic_score": 0.72,
  "recency_score": 1.0,
  "importance_score": 0.8,
  "source_score": 1.0,
  "final_score": 0.788,
  "estimated_tokens": 25,
  "compressed": false,
  "reason": "0.65 relevance + 0.15 recency + 0.15 importance + 0.05 source; budget fit"
}
```

This fragment illustrates the response shape. Actual measured output, including identifiers and complete packages, is in [docs/demo-output.json](docs/demo-output.json).

## MCP example

With the REST server running, start the official SDK stdio adapter:

```bash
python -m app.mcp.server
```

Configure an MCP client with:

```json
{
  "mcpServers": {
    "agent-context-broker": {
      "command": "/absolute/path/to/agent-context-broker/.venv/bin/python",
      "args": ["-m", "app.mcp.server"],
      "env": {"BROKER_URL": "http://127.0.0.1:8000"}
    }
  }
}
```

Create a session through REST, then invoke `calculator` with `{"session_id":"...","expression":"(12+8)*3"}`. MCP tool discovery also includes `text_search`, `memory_lookup`, `system_status`, and `build_context`. The adapter persists traces through the broker; it does not keep a second in-memory session store. Domain tool failures surface as MCP `isError` responses.

## Model routing

No key configured: the mock provider returns the query and retrieved evidence deterministically. It is a test fixture, not an LLM.

For a real provider, set `BROKER_OPENAI_API_KEY`, `BROKER_OPENAI_BASE_URL`, and `BROKER_OPENAI_MODEL`. Optional input/output prices are operator-supplied dollars per million estimated tokens. Missing prices produce a null cost estimate. `POST /models/route` accepts `preferred_model`, `policy` (`balanced`, `latency`, `cost`), and `task_type`.

Routing uses configured latency estimates and prices, not learned performance statistics. With a real provider configured, mock fallback requires explicit `allow_mock_fallback: true`; an unavailable real model otherwise returns 503. External model requests can incur charges again if retried. Response records include attempted providers, elapsed latency, token estimates, and configured cost estimates.

## Measured results

The committed baseline ran against the actual service over loopback HTTP: **1,200 requests per profile, concurrency 10, one Uvicorn worker, 0% errors**. The mixed workload creates sessions, writes eight memories, builds cold/hot context, and executes a calculator tool.

| Profile | p50 | p95 | Requests/sec |
| --- | ---: | ---: | ---: |
| SQLite + local cache | 12.59 ms | 136.91 ms | 249.35 |
| SQLite + Redis | 13.07 ms | 132.29 ms | 243.87 |

These are local measurements, not production capacity claims or a PostgreSQL benchmark. The [benchmark report](docs/benchmarks.md) records the exact environment, workload, and raw results. Rerunning may produce different values.

The long-session demo reduced estimated context tokens from **1,141 to 210 (81.6%)**, removing 20 duplicate items from 35 candidates. The four-case synthetic evaluation retained all labeled relevant groups and required facts, with no selected duplicates or irrelevant items and a mean estimated token reduction of **47.3%**. These tiny, deliberately simple cases measure pipeline behavior, not general agent intelligence or hallucinations.

## Project structure

```text
app/
  api/             FastAPI application and dependency container
  core/            configuration and domain errors
  models/          typed requests and responses
  repositories/    async SQL storage and cache protocols/adapters
  providers/       embedding and model interfaces
  services/        sessions, context ranking, tools, model routing
  mcp/             official SDK stdio bridge
  evaluation/      synthetic dataset and evaluator
  observability/   metrics and request correlation
scripts/           end-to-end demo, evaluation, real HTTP benchmark
tests/             API, ranking, reliability, MCP, real-service tests
docs/              architecture, measurements, interview guide
```

## Design decisions and boundaries

- PostgreSQL is the source of truth. Redis is never required for durable writes or recovery.
- An event write and revision increment commit together. Unique `(session_id, idempotency_key)` constraints make concurrent retries return one event; different payloads return 409. Session creation and external tool/model calls are not idempotent.
- Context cache keys include revision, request parameters, pipeline version, and embedding identity. A revision check prevents caching a context build that overlapped a writer. Context builds are bounded reads, not serializable snapshots of an indefinitely growing history.
- Hash vectors make tests reproducible offline. They capture lexical overlap and can collide; they do not replace learned semantic embeddings. Stored vectors need a rebuild if the embedding provider changes.
- Candidate retrieval scans the newest 2,000 events by default. It can omit older important memories; use indexed vector retrieval before scaling long histories. Deduplication is quadratic in the number of retained candidates.
- Compression removes duplicates and truncates lower-ranked text. There is no LLM summarization, and important facts can be cut under a tight budget.
- The context budget applies to the returned rendered evidence using a UTF-8 bytes/4 estimate. It excludes the query, system prompt, and model output; use a model tokenizer before claiming exact limits or billing savings.
- Tool registration is trusted Python application code. There is no arbitrary uploaded code, remote URL registration, shell execution, or external MCP-server federation.
- Transient read-only failures retry with exponential backoff. Timeouts and non-read-only tools do not retry. A crash after an external side effect can leave a started call without a completion record. No exactly-once execution claim.
- API key support is a single trusted-service boundary, not per-user authorization. Sessions are scoped by ID, with no multi-tenant ACLs. Put TLS, body-size limits for chunked requests, and rate limits at a gateway before public deployment.
- Metrics use bounded labels; identifiers live in logs and persisted traces. No OpenTelemetry exporter, distributed trace propagation, queue worker, or distributed lock is implemented.
- Tables are initialized at startup for this initial schema. Versioned migrations are needed before evolving a deployed database.

## Validation and roadmap

Local validation: 42 tests passed with 93% application coverage, including a real Redis sharing test. PostgreSQL tests require a service; Docker was unavailable locally. CI contains actual PostgreSQL/Redis integration and Docker Compose smoke tests. See [verification notes](docs/verification.md) for the final recorded state.

Next: learned embedding provider + pgvector retrieval, explicit memory namespace/retention controls, model tokenizers, database migrations, multi-tenant authorization, remote MCP allowlisting, OTLP traces, and workload-specific evaluations with human labels.

## License

MIT. See [LICENSE](LICENSE).
