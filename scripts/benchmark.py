"""Real HTTP closed-loop load generator, not in-process ASGI transport."""

import argparse
import asyncio
import json
import os
import platform
import time
from collections import defaultdict
from pathlib import Path

import httpx


def percentile(values, fraction):
    ordered = sorted(values)
    return (
        ordered[max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction)))] if ordered else 0
    )


async def benchmark(args):
    latencies = defaultdict(list)
    errors = 0
    requests = 0
    context_stats = []
    semaphore = asyncio.Semaphore(args.concurrency)
    headers = (
        {"Authorization": f"Bearer {os.environ['BROKER_API_KEY']}"}
        if os.getenv("BROKER_API_KEY")
        else {}
    )
    async with httpx.AsyncClient(
        base_url=args.url,
        headers=headers,
        timeout=30,
        trust_env=False,
        limits=httpx.Limits(max_connections=args.concurrency),
    ) as client:
        health = (await client.get("/health")).json()

        async def request(operation, path, payload, measure):
            nonlocal errors, requests
            start = time.perf_counter()
            try:
                response = await client.post(path, json=payload)
                response.raise_for_status()
                result = response.json()
                if operation == "tool" and not result["success"]:
                    raise RuntimeError("Tool failed")
            except Exception:
                if measure:
                    errors += 1
                raise
            finally:
                if measure:
                    requests += 1
                    latencies[operation].append((time.perf_counter() - start) * 1000)
            return result

        async def flow(index, measure=True):
            async with semaphore:
                try:
                    sid = (
                        await request(
                            "session",
                            "/sessions",
                            {"agent_id": f"load-{index}", "token_budget": 256},
                            measure,
                        )
                    )["session_id"]
                    for i in range(8):
                        await request(
                            "memory",
                            f"/sessions/{sid}/memory",
                            {
                                "content": (
                                    "Redis cache invalidation requires session revision "
                                    "in context cache keys."
                                    if i % 2 == 0
                                    else f"Debugging note {i}: Redis cache stale context "
                                    "after writes."
                                ),
                            },
                            measure,
                        )
                    context = await request(
                        "context_cold",
                        "/context/build",
                        {"session_id": sid, "query": "Redis cache stale context revision"},
                        measure,
                    )
                    await request(
                        "context_hot",
                        "/context/build",
                        {"session_id": sid, "query": "Redis cache stale context revision"},
                        measure,
                    )
                    await request(
                        "tool",
                        "/tools/calculator/execute",
                        {"session_id": sid, "arguments": {"expression": "(8+4)*3"}},
                        measure,
                    )
                    if measure:
                        context_stats.append(context)
                except Exception:
                    return  # failed requests are counted; workflow stops if session creation fails

        for i in range(args.warmup):
            await flow(f"warmup-{i}", False)
        start = time.perf_counter()
        await asyncio.gather(*(flow(i) for i in range(args.flows)))
        elapsed = time.perf_counter() - start
    all_latencies = [value for values in latencies.values() for value in values]
    return {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "service": health,
            "transport": "loopback HTTP",
            "server_workers": args.server_workers,
        },
        "concurrency": args.concurrency,
        "flows": args.flows,
        "warmup_flows": args.warmup,
        "request_count": requests,
        "expected_request_count": args.flows * 12,
        "duration_seconds": elapsed,
        "throughput_requests_per_second": requests / elapsed,
        "p50_ms": percentile(all_latencies, 0.5),
        "p95_ms": percentile(all_latencies, 0.95),
        "errors": errors,
        "error_rate": errors / max(1, requests),
        "by_operation": {
            op: {
                "requests": len(values),
                "p50_ms": percentile(values, 0.5),
                "p95_ms": percentile(values, 0.95),
            }
            for op, values in latencies.items()
        },
        "mean_estimated_token_reduction": sum(
            1 - c["estimated_tokens_after"] / max(1, c["estimated_tokens_before"])
            for c in context_stats
        )
        / max(1, len(context_stats)),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--flows", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--server-workers", type=int, default=1)
    parser.add_argument("--output", default="docs/benchmark-results.json")
    args = parser.parse_args()
    if args.concurrency < 1 or args.flows < 1:
        parser.error("concurrency and flows must be positive")
    result = asyncio.run(benchmark(args))
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
