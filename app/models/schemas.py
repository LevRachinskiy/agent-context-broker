from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class EventKind(StrEnum):
    user = "user"
    assistant = "assistant"
    tool_call = "tool_call"
    tool_response = "tool_response"
    memory = "memory"
    error = "error"
    model_response = "model_response"


class SessionCreate(BaseModel):
    agent_id: str = Field(min_length=1, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)
    token_budget: int = Field(default=2048, ge=32, le=100000)


class Session(SessionCreate):
    session_id: str
    created_at: datetime
    updated_at: datetime
    revision: int


class EventCreate(BaseModel):
    kind: EventKind
    content: str = Field(min_length=1, max_length=16000)
    importance: float = Field(default=0.5, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=16000)
    importance: float = Field(default=0.8, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Event(EventCreate):
    event_id: str
    session_id: str
    created_at: datetime
    embedding: list[float] = Field(exclude=True)


class ContextRequest(BaseModel):
    session_id: str
    query: str = Field(min_length=1, max_length=16000)
    token_budget: int | None = Field(default=None, ge=32, le=100000)
    duplicate_threshold: float = Field(default=0.94, ge=0.5, le=1)
    min_relevance: float = Field(default=0.08, ge=0, le=1)


class ContextItem(BaseModel):
    event_id: str
    content: str
    source: EventKind
    semantic_score: float
    recency_score: float
    importance_score: float
    source_score: float
    final_score: float
    estimated_tokens: int
    compressed: bool
    reason: str


class ContextPackage(BaseModel):
    session_id: str
    revision: int
    query: str
    items: list[ContextItem]
    text: str
    considered: int
    selected: int
    duplicates_removed: int
    irrelevant_removed: int
    budget_dropped: int
    estimated_tokens_before: int
    estimated_tokens_after: int
    token_budget: int
    candidate_limit: int
    cache_hit: bool = False


class ToolRequest(BaseModel):
    session_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    trace_id: str
    tool: str
    success: bool
    result: Any = None
    error: dict[str, str] | None = None
    attempts: int
    latency_ms: float


class RouteRequest(ContextRequest):
    preferred_model: str | None = None
    policy: str = Field(default="balanced", pattern="^(balanced|latency|cost)$")
    task_type: str = Field(default="general", max_length=64)
    allow_mock_fallback: bool = False


class ModelResult(BaseModel):
    provider: str
    model: str
    content: str
    latency_ms: float
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost: float | None
    attempts: list[str]
    context: ContextPackage


class StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
