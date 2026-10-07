from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BROKER_", env_file=".env", extra="ignore")
    database_url: str = "sqlite+aiosqlite:///./broker.db"
    redis_url: str | None = None
    api_key: str | None = None
    cache_ttl: int = Field(default=120, ge=1)
    candidate_limit: int = Field(default=2000, ge=1, le=10000)
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    input_cost_per_million: float = Field(default=0.0, ge=0)
    output_cost_per_million: float = Field(default=0.0, ge=0)
