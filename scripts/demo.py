import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx


async def demo(client):
    async def post(path, payload):
        response = await client.post(path, json=payload)
        response.raise_for_status()
        return response.json()

    session = await post("/sessions", {"agent_id": "debugging-agent", "token_budget": 256})
    sid = session["session_id"]
    for text, key in [
        (
            "Redis context cache keys must include the session revision to prevent stale context.",
            "cache",
        ),
        ("Postgres persists events and increments session revision in one transaction.", "storage"),
        (
            "Earlier debugging attempt: clearing Redis temporarily fixed stale context, "
            "but the bug returned after writes.",
            "attempt",
        ),
    ]:
        await post(f"/sessions/{sid}/memory", {"content": text, "metadata": {"key": key}})
    tool = await post(
        "/tools/memory_lookup/execute", {"session_id": sid, "arguments": {"key": "cache"}}
    )
    for i in range(30):
        text = (
            "Redis context cache keys must include the session revision to prevent stale context."
            if i % 3 != 0
            else "Unrelated meeting notes: discuss lunch menu and office parking."
        )
        await post(f"/sessions/{sid}/events", {"kind": "user", "content": text})
    query = "Why does Redis context cache return stale context after session writes?"
    context = await post("/context/build", {"session_id": sid, "query": query})
    hit = await post("/context/build", {"session_id": sid, "query": query})
    model = await post(
        "/models/route",
        {
            "session_id": sid,
            "query": query,
            "preferred_model": "mock-grounded-v1",
            "allow_mock_fallback": True,
        },
    )
    result = {
        "session_id": sid,
        "tool_trace": tool,
        "cache_hit_on_repeat": hit["cache_hit"],
        "context": context,
        "model": {k: v for k, v in model.items() if k != "context"},
    }
    print(json.dumps(result, indent=2))
    return result


async def main(args):
    headers = (
        {"Authorization": f"Bearer {os.environ['BROKER_API_KEY']}"}
        if os.getenv("BROKER_API_KEY")
        else {}
    )
    async with httpx.AsyncClient(
        base_url=args.url, headers=headers, timeout=30, trust_env=False
    ) as client:
        result = await demo(client)
    if args.output:
        await asyncio.to_thread(Path(args.output).write_text, json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output")
    asyncio.run(main(parser.parse_args()))
