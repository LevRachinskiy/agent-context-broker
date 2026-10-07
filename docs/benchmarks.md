# Measured benchmarks

Recorded 2026-10-07 UTC against the real application over loopback HTTP, not an in-process ASGI client. The generator and service run in the same execution container.

## Environment

```json
{
  "platform": "Linux-6.18.44-x86_64-with-glibc2.39",
  "python": "3.12.14",
  "cpu_count": 9,
  "service": {
    "status": "ok",
    "storage": "sqlite",
    "cache": "local"
  },
  "transport": "loopback HTTP",
  "server_workers": 1
}
```

Redis profile: actual Redis 7.0.15 subprocess; PostgreSQL is not part of either measured profile. Dependency versions are pinned in `uv.lock`. CPU count reports visible logical CPUs, not dedicated capacity. One Uvicorn worker handled requests.

## Workload

Each of 100 independent sessions creates a session, writes eight memories with duplicates, builds cold context, repeats the same build for a cache hit, and executes a calculator tool. Five warmup flows are excluded. Closed-loop concurrency is 10 workflows. Total measured requests: 1,200 per profile. The dataset per session is intentionally small; this does not stress the 2,000-candidate ceiling.

| Profile | Requests | p50 ms | p95 ms | Requests/sec | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| SQLite + local cache | 1200 | 12.59 | 136.91 | 249.35 | 0 |
| SQLite + Redis | 1200 | 13.07 | 132.29 | 243.87 | 0 |

## Per-operation latency

| Profile | Operation | Requests | p50 ms | p95 ms |
| --- | --- | ---: | ---: | ---: |
| Local cache | session | 100 | 9.50 | 95.78 |
| Local cache | memory | 800 | 10.83 | 137.95 |
| Local cache | context_cold | 100 | 27.25 | 44.22 |
| Local cache | context_hot | 100 | 12.42 | 22.06 |
| Local cache | tool | 100 | 38.31 | 260.29 |
| Redis | session | 100 | 9.55 | 335.30 |
| Redis | memory | 800 | 11.27 | 137.54 |
| Redis | context_cold | 100 | 27.20 | 44.14 |
| Redis | context_hot | 100 | 12.41 | 20.43 |
| Redis | tool | 100 | 36.65 | 152.11 |

Mean estimated token reduction in both mixed workloads: 40.2%. The long-session demo is a different workload: 35 candidates, 20 duplicates removed, 5 selected items, 1,141 to 210 estimated tokens (81.6% reduction).

## Reproduce

Start the server in one terminal and run:

```bash
python scripts/benchmark.py --concurrency 10 --flows 100 --warmup 5 --output docs/benchmark-local.json
```

For Redis, configure `BROKER_REDIS_URL` and restart the server before running the same command with a different output path. A Docker Compose run uses PostgreSQL and must be reported as a separate measurement. Do not reuse the committed SQLite numbers for that deployment.

Raw results: [local](benchmark-local.json), [Redis](benchmark-redis.json). Throughput counts HTTP requests, not workflows or agent tasks. The p95 tail reflects the combined workload, including SQLite write contention. Cache-hit metrics isolate the benefit more clearly than the mixed average. This is one run per profile, not a statistically controlled comparison. Reruns can vary significantly.
