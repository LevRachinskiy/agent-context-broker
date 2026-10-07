"""Official MCP SDK stdio adapter. Durable execution stays inside the REST broker."""

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Agent Context Broker")


async def broker_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    headers = {}
    if key := os.environ.get("BROKER_API_KEY"):
        headers["Authorization"] = f"Bearer {key}"
    async with httpx.AsyncClient(
        base_url=os.environ.get("BROKER_URL", "http://127.0.0.1:8000"),
        headers=headers,
        timeout=30,
        trust_env=False,
    ) as client:
        response = await client.post(path, json=payload)
        response.raise_for_status()
        return response.json()


async def invoke(tool: str, session_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await broker_post(
        f"/tools/{tool}/execute", {"session_id": session_id, "arguments": arguments}
    )
    if not result["success"]:
        raise ValueError(f"{result['error']['code']}: {result['error']['message']}")
    return result


@mcp.tool()
async def calculator(session_id: str, expression: str) -> dict[str, Any]:
    """Evaluate bounded arithmetic and persist its execution trace."""
    return await invoke("calculator", session_id, {"expression": expression})


@mcp.tool()
async def text_search(session_id: str, query: str) -> dict[str, Any]:
    """Find literal matches in durable session events."""
    return await invoke("text_search", session_id, {"query": query})


@mcp.tool()
async def memory_lookup(session_id: str, key: str) -> dict[str, Any]:
    """Look up the latest durable memory with a matching metadata key."""
    return await invoke("memory_lookup", session_id, {"key": key})


@mcp.tool()
async def system_status(session_id: str) -> dict[str, Any]:
    """Return session status through the traced tool executor."""
    return await invoke("system_status", session_id, {})


@mcp.tool()
async def build_context(session_id: str, query: str, token_budget: int = 2048) -> dict[str, Any]:
    """Select context with inspectable scores, deduplication and a token budget."""
    return await broker_post(
        "/context/build", {"session_id": session_id, "query": query, "token_budget": token_budget}
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
