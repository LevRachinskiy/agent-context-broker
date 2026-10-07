# Interview questions and architectural answers

## 1. How do you prevent stale context when multiple instances write concurrently?

The SQL transaction inserts the event and atomically increments its session revision. Cache keys include that revision, so a write produces a new namespace without relying on a potentially lost invalidation message. A cache miss checks the revision again before writing its result. This still costs a database read on hits and does not provide a strict serializable snapshot for an overlapping uncached build.

## 2. What exactly is idempotent, and what happens if a process crashes after a tool side effect?

Event/memory inserts are idempotent through a unique `(session_id, idempotency_key)` constraint and a canonical payload fingerprint. Concurrent identical retries return one event; changed payloads return 409. Tool execution is not exactly once: a crash after a side effect can leave only the started-call event. Timeouts are not blindly retried, and non-read-only tools get one attempt. Production side-effecting tools would need provider-supported idempotency or reconciliation.

## 3. Why use hash embeddings, and where will retrieval break?

They make the system reproducible and runnable without credentials. They measure lexical overlap through normalized, signed feature hashing, not learned semantic similarity. Paraphrases can miss, unrelated tokens can collide, and the newest-2,000-event window can discard important older memory. The interface supports replacing the embedder, but stored vectors must be rebuilt consistently. Indexed retrieval and separate durable-memory policies would be the next changes.

## 4. How do you know compression preserves useful information?

The evaluator labels relevant equivalence groups and independently checks required fact substrings in the rendered result, so selecting the correct item is not enough if truncation removes its fact. The four simple cases retained all labels/facts and reduced estimated tokens by 47.3% on average. This is a small synthetic check, not proof of general context quality, hallucination reduction, or model performance. Tight-budget and held-out paraphrase cases would be needed next.

## 5. What is the bottleneck, and how would you scale this system?

SQLite serializes writes and local load tests show a substantial latency tail. Retrieval scans a bounded candidate window, and duplicate comparison can be quadratic. PostgreSQL moves durability to a shared service, Redis shares cached packages, but those changes alone do not guarantee throughput. I would profile the real PostgreSQL workload, add indexed vector candidate search, use a dedicated durable-memory namespace, benchmark multiple processes, then introduce queues only for work that benefits from asynchronous execution.

## Resume bullets supported by this implementation

- Built a FastAPI context broker with persistent agent sessions, pluggable storage and model providers, MCP tool execution, revision-aware caching, and inspectable context ranking and failure recovery.
- Added automated evaluation, Prometheus metrics, and concurrent HTTP load testing; reduced estimated context tokens by 81.6% in a long-session demo and completed 1,200 requests at concurrency 10 with zero errors in each measured local profile.

Use the demo percentage only with its scope. Do not call lexical hashing learned semantic retrieval, estimated tokens real billed savings, or local SQLite throughput production distributed-system throughput.
