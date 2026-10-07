from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "broker_requests_total", "HTTP requests", ["route", "status"], registry=self.registry
        )
        self.latency = Histogram(
            "broker_request_seconds", "HTTP latency", ["route"], registry=self.registry
        )
        self.tools = Counter(
            "broker_tool_calls_total", "Tool outcomes", ["tool", "success"], registry=self.registry
        )
        self.tool_latency = Histogram(
            "broker_tool_seconds", "Tool latency", ["tool"], registry=self.registry
        )
        self.context = Counter(
            "broker_context_items_total", "Context selection", ["stage"], registry=self.registry
        )
        self.tokens = Counter(
            "broker_context_tokens_total", "Token estimates", ["stage"], registry=self.registry
        )
        self.cache = Counter(
            "broker_cache_total", "Cache outcomes", ["outcome"], registry=self.registry
        )
        self.cache_errors = Counter(
            "broker_cache_errors_total", "Redis failures", registry=self.registry
        )
        self.models = Counter(
            "broker_model_requests_total",
            "Model attempts",
            ["provider", "outcome"],
            registry=self.registry,
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)
