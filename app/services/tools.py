import ast
import asyncio
import json
import math
import operator
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from pydantic import Field

from app.core.errors import InvalidToolInput, NotFound
from app.models.schemas import (
    EventCreate,
    EventKind,
    StrictArguments,
    ToolRequest,
    ToolResult,
    new_id,
)
from app.observability.metrics import Metrics
from app.observability.tracing import request_id_context
from app.repositories.sql import Repository
from app.services.sessions import SessionService


class TransientToolError(Exception):
    """Only this explicitly retryable failure triggers retries for read-only tools."""


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable[[str, dict[str, Any]], Awaitable[Any]]
    timeout: float = 2.0
    read_only: bool = True
    max_attempts: int = 2


class CalculatorArguments(StrictArguments):
    expression: str = Field(min_length=1, max_length=200)


class SearchArguments(StrictArguments):
    query: str = Field(min_length=1, max_length=200)


class LookupArguments(StrictArguments):
    key: str = Field(min_length=1, max_length=128)


class EmptyArguments(StrictArguments):
    pass


def calculate(expression: str) -> float:
    """Bounded arithmetic AST: no names, calls, attributes, powers, or Python eval."""
    tree = ast.parse(expression, mode="eval")
    if len(list(ast.walk(tree))) > 40:
        raise ValueError("Expression too complex")
    operations = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
    }

    def walk(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            value = walk(node.body)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)
        ):
            value = float(node.value)
        elif isinstance(node, ast.BinOp) and type(node.op) in operations:
            value = operations[type(node.op)](walk(node.left), walk(node.right))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = walk(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        else:
            raise ValueError("Only numbers and +, -, *, / are supported")
        if not math.isfinite(value) or abs(value) > 1e12:
            raise ValueError("Result outside allowed range")
        return value

    return walk(tree)


class ToolService:
    def __init__(self, repository: Repository, sessions: SessionService, metrics: Metrics):
        self.repository, self.sessions, self.metrics = repository, sessions, metrics
        self.registry: dict[str, ToolSpec] = {}
        self.register(
            ToolSpec(
                "calculator",
                "Bounded arithmetic",
                CalculatorArguments.model_json_schema(),
                self._calculator,
            )
        )
        self.register(
            ToolSpec(
                "text_search",
                "Literal search over session events",
                SearchArguments.model_json_schema(),
                self._search,
            )
        )
        self.register(
            ToolSpec(
                "memory_lookup",
                "Latest memory with metadata.key",
                LookupArguments.model_json_schema(),
                self._lookup,
            )
        )
        self.register(
            ToolSpec(
                "system_status",
                "Broker and session status",
                EmptyArguments.model_json_schema(),
                self._status,
            )
        )

    def register(self, tool: ToolSpec) -> None:
        """Trusted application registration, never execute uploaded code or arbitrary URLs."""
        Draft202012Validator.check_schema(tool.schema)
        if tool.name in self.registry:
            raise ValueError("Duplicate tool name")
        if not 0 < tool.timeout <= 30 or not 1 <= tool.max_attempts <= 3:
            raise ValueError("Invalid execution limits")
        self.registry[tool.name] = tool

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.schema,
                "annotations": {"readOnlyHint": t.read_only},
            }
            for t in self.registry.values()
        ]

    async def execute(self, name: str, request: ToolRequest) -> ToolResult:
        spec = self.registry.get(name)
        if spec is None:
            raise NotFound("Tool not found")
        try:
            Draft202012Validator(spec.schema).validate(request.arguments)
        except ValidationError as error:
            raise InvalidToolInput(error.message) from None
        await self.repository.get_session(request.session_id)
        trace_id, start = new_id(), time.perf_counter()
        await self.sessions.append(
            request.session_id,
            EventCreate(
                kind=EventKind.tool_call,
                content=json.dumps({"tool": name, "arguments": request.arguments}),
                metadata={
                    "trace_id": trace_id,
                    "tool": name,
                    "request_id": request_id_context.get(),
                },
            ),
        )
        result: Any = None
        failure: dict[str, str] | None = None
        attempts = 0
        limit = spec.max_attempts if spec.read_only else 1
        for attempt in range(1, limit + 1):
            attempts = attempt
            try:
                result = await asyncio.wait_for(
                    spec.handler(request.session_id, request.arguments), timeout=spec.timeout
                )
                failure = None
                break
            except TransientToolError:
                failure = {"code": "transient_failure", "message": "Tool temporarily unavailable"}
                if attempt < limit:
                    await asyncio.sleep(0.025 * 2 ** (attempt - 1))
            except TimeoutError:
                failure = {"code": "timeout", "message": "Tool exceeded its time limit"}
                break  # Timeout could follow an external side effect; never retry blindly.
            except Exception:
                failure = {"code": "execution_failed", "message": "Tool execution failed"}
                break
        elapsed = (time.perf_counter() - start) * 1000
        outcome = ToolResult(
            trace_id=trace_id,
            tool=name,
            success=failure is None,
            result=result,
            error=failure,
            attempts=attempts,
            latency_ms=elapsed,
        )
        await self.sessions.append(
            request.session_id,
            EventCreate(
                kind=EventKind.tool_response if outcome.success else EventKind.error,
                content=outcome.model_dump_json()[:16000],
                importance=0.7,
                metadata={
                    "trace_id": trace_id,
                    "tool": name,
                    "success": outcome.success,
                    "request_id": request_id_context.get(),
                },
            ),
        )
        self.metrics.tools.labels(name, str(outcome.success).lower()).inc()
        self.metrics.tool_latency.labels(name).observe(elapsed / 1000)
        return outcome

    async def _calculator(self, session_id: str, arguments: dict[str, Any]) -> Any:
        return {"value": calculate(arguments["expression"])}

    async def _search(self, session_id: str, arguments: dict[str, Any]) -> Any:
        events = await self.repository.list_events(session_id)
        query = arguments["query"].casefold()
        return [
            {"event_id": e.event_id, "content": e.content}
            for e in events
            if query in e.content.casefold() and e.kind != EventKind.tool_call
        ][:20]

    async def _lookup(self, session_id: str, arguments: dict[str, Any]) -> Any:
        events = await self.repository.list_events(session_id, kind=EventKind.memory)
        found = next((e for e in events if e.metadata.get("key") == arguments["key"]), None)
        return {"found": found is not None, "content": found.content if found else None}

    async def _status(self, session_id: str, arguments: dict[str, Any]) -> Any:
        session = await self.repository.get_session(session_id)
        return {"status": "ok", "session_id": session_id, "revision": session.revision}
