# Architecture and reliability

The broker is an async FastAPI service with typed domain schemas, SQLAlchemy storage, replaceable cache/embedding/model protocols, and separate context/tool/model services. The official MCP adapter talks to REST so it reuses durability, validation, authentication, and instrumentation.

## Write path

1. Validate an event or memory payload and compute its embedding.
2. In a database transaction, increment the session revision and insert the event.
3. Commit both changes together. For a duplicate idempotency key, roll back the transaction and read the existing event. Reject a mismatched payload fingerprint with 409.

The SQL `UPDATE ... RETURNING` locks/serializes changes to a session row in PostgreSQL; unique constraints enforce idempotency across processes. SQLite is useful for development but serializes database writes and is not the scaling profile. Idempotency is durable and scoped to event/memory insertion, not external side effects.

## Context path

1. Read session metadata/revision and clamp the requested context budget to the session cap.
2. Form a cache key from revision, request parameters, embedding identity, candidate limit, and pipeline version.
3. On a miss, load at most the newest configured candidate count and embed the query.
4. Score each item: `0.65 × relevance + 0.15 × exp(-age_hours/168) + 0.15 × importance + 0.05 × source_weight`.
5. Filter low relevance; compare normalized text and cosine similarity against retained candidates; greedily fit ranked content into the budget.
6. Return rendered evidence and per-item explanations, with all removal/token counters.
7. Recheck the revision before caching. If a writer committed during the build, do not store that package under the old revision.

A cached read can linearize at the initial revision read. An uncached build can see writes that overlap its candidate query, so its revision is the starting version and should not be treated as a strict database snapshot identifier. The second read prevents this mixed package becoming a stale cache entry.

Cache unavailability changes latency but not durable history. There is no local fallback copy of Redis data, which avoids returning stale data from a second cache layer. Hot requests still read the database revision, trading a database round trip for freshness without an invalidation bus.

## Tools and models

A tool call gets a UUID trace ID and a durable `tool_call` event before execution. Schema validation precedes execution. The executor times out cooperative async handlers, retries only explicit transient read-only failures, and stores success/error outcomes. SDK MCP failures become `isError` results. Registered handlers are trusted code, not isolated plugins.

The model router builds context, orders eligible providers by configured policy/preference, tries each provider up to twice, and persists the response or exhausted-provider error. Attempts and price estimates are visible. External-provider request failures are sanitized. Mock fallback must be opted into when real providers exist.

## Tradeoffs

| Choice | Benefit | Limit |
| --- | --- | --- |
| SQL source of truth | Atomic revision/event writes, durable idempotency | Database round trip on cache hits |
| Recent candidate window | Bounded retrieval cost | Old important memory can disappear from context |
| Hash embeddings | Offline deterministic execution | Lexical overlap, collisions, weak paraphrase matching |
| Greedy selection | Explainable, reproducible budget fit | Can truncate a crucial fact and is not optimal knapsack selection |
| Stdio MCP bridge | Real protocol with one execution path | Extra local REST hop and no remote MCP federation |
| Single service | Easy to run and inspect | No worker queue, distributed locks, or failover deployment |

## Security and operating scope

The default server binds localhost. Compose does not expose PostgreSQL or Redis publicly. Optional bearer authentication protects service calls with one shared credential. This is appropriate for a trusted local/service deployment, not a public multi-tenant platform. A gateway must add TLS, rate limits, and streamed body caps. The application limits declared request bodies and typed content sizes.

Context and stored tool results are untrusted data. The compatible model adapter asks the model to treat evidence as data, but no prompt-injection prevention guarantee is made. Calculator execution parses a bounded arithmetic AST without `eval`, calls, names, attribute access, or exponentiation.

## Scaling next

Move candidate retrieval into indexed vector search, isolate durable memories from the recent event window, introduce versioned migrations/embedding backfills, and add per-tenant ACLs. Use model-specific tokenizers and better evaluation corpora before promising context quality or cost improvements. Add an outbox/worker design only when actual asynchronous execution needs it.
