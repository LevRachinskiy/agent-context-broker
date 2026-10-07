import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.fixture
def live_server(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "BROKER_DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path}/mcp.db",
        "BROKER_API_KEY": "mcp-test-key",
        "BROKER_REDIS_URL": "",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                if httpx.get(url + "/health", timeout=0.1, trust_env=False).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        else:
            raise RuntimeError("Server did not start")
        yield url
    finally:
        process.terminate()
        process.wait(timeout=5)


async def test_actual_mcp_handshake_discovery_execution_and_errors(live_server):
    async with httpx.AsyncClient(
        base_url=live_server, trust_env=False, headers={"Authorization": "Bearer mcp-test-key"}
    ) as client:
        sid = (await client.post("/sessions", json={"agent_id": "mcp-agent"})).json()["session_id"]
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.server"],
        env={**os.environ, "BROKER_URL": live_server, "BROKER_API_KEY": "mcp-test-key"},
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {t.name for t in tools.tools} == {
                "calculator",
                "text_search",
                "memory_lookup",
                "system_status",
                "build_context",
            }
            success = await session.call_tool(
                "calculator", {"session_id": sid, "expression": "3*7"}
            )
            assert not success.isError
            invalid = await session.call_tool(
                "calculator", {"session_id": sid, "expression": "1/0"}
            )
            assert invalid.isError
            status = await session.call_tool("system_status", {"session_id": sid})
            assert not status.isError
            context = await session.call_tool(
                "build_context", {"session_id": sid, "query": "calculator"}
            )
            assert not context.isError
            missing = await session.call_tool(
                "memory_lookup", {"session_id": sid, "key": "unknown"}
            )
            assert not missing.isError
            search = await session.call_tool(
                "text_search", {"session_id": sid, "query": "calculator"}
            )
            assert not search.isError
