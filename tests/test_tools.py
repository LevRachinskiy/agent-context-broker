import asyncio

import pytest

from app.models.schemas import ToolRequest
from app.services.tools import ToolSpec, TransientToolError, calculate


@pytest.mark.parametrize("expression,expected", [("(2+3)*4", 20), ("-8 / 2", -4), ("+3", 3)])
def test_calculator(expression, expected):
    assert calculate(expression) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('id')",
        "2**99999",
        "True",
        "1 / 0",
        "1e999",
        "9 * 999999999999",
        "x.y",
    ],
)
def test_calculator_rejects_unsafe_input(expression):
    with pytest.raises((ValueError, ZeroDivisionError)):
        calculate(expression)


async def test_tool_validation_and_trace(client, session_id):
    invalid = await client.post(
        "/tools/calculator/execute",
        json={"session_id": session_id, "arguments": {"expression": 42}},
    )
    assert invalid.status_code == 422
    extra = await client.post(
        "/tools/calculator/execute",
        json={"session_id": session_id, "arguments": {"expression": "2+2", "unexpected": True}},
    )
    assert extra.status_code == 422
    valid = await client.post(
        "/tools/calculator/execute",
        json={"session_id": session_id, "arguments": {"expression": "2+2"}},
    )
    assert valid.json()["result"]["value"] == 4
    events = (await client.get(f"/sessions/{session_id}/events")).json()
    assert {e["kind"] for e in events} == {"tool_call", "tool_response"}
    assert all(e["metadata"]["trace_id"] == valid.json()["trace_id"] for e in events)
    assert (
        await client.post("/tools/unknown/execute", json={"session_id": session_id})
    ).status_code == 404


async def test_timeout_and_retry(client, session_id):
    service = client.broker.tools
    calls = 0

    async def flaky(session, arguments):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransientToolError()
        return "recovered"

    async def slow(session, arguments):
        await asyncio.sleep(0.1)

    service.register(ToolSpec("flaky", "test", {"type": "object"}, flaky))
    service.register(ToolSpec("slow", "test", {"type": "object"}, slow, timeout=0.01))
    recovered = await service.execute("flaky", ToolRequest(session_id=session_id))
    assert recovered.success and recovered.attempts == 2
    timed_out = await service.execute("slow", ToolRequest(session_id=session_id))
    assert not timed_out.success and timed_out.error["code"] == "timeout"
    assert timed_out.attempts == 1
    service.register(ToolSpec("mutation", "test", {"type": "object"}, flaky, read_only=False))
    calls = 0
    failed = await service.execute("mutation", ToolRequest(session_id=session_id))
    assert not failed.success and failed.attempts == 1


async def test_builtin_lookup_search_status(client, session_id):
    await client.post(
        f"/sessions/{session_id}/memory",
        json={"content": "Uses PostgreSQL", "metadata": {"key": "database"}},
    )
    for name, arguments in [
        ("memory_lookup", {"key": "database"}),
        ("text_search", {"query": "PostgreSQL"}),
        ("system_status", {}),
    ]:
        result = (
            await client.post(
                f"/tools/{name}/execute", json={"session_id": session_id, "arguments": arguments}
            )
        ).json()
        assert result["success"]
    invalid_math = (
        await client.post(
            "/tools/calculator/execute",
            json={"session_id": session_id, "arguments": {"expression": "1/0"}},
        )
    ).json()
    assert not invalid_math["success"] and invalid_math["error"]["code"] == "execution_failed"
