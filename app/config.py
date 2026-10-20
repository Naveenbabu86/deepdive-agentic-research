"""Central, validated application configuration."""
from functools import lru_cache
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    mesh_api_key: str = ""
    mesh_model: str = "openai/gpt-4o"
    mesh_base_url: str = "https://api.meshapi.ai/v1"
    tavily_api_key: str = ""
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "deepdive_evidence"
    qdrant_top_k: int = Field(default=8, ge=1, le=30)
    redis_url: str = ""
    llm_cache_ttl_seconds: int = Field(default=1800, ge=1, le=604800)
    redis_memory_ttl_seconds: int = Field(default=2592000, ge=60, le=31536000)
    redis_memory_max_runs: int = Field(default=5, ge=1, le=20)
    database_path: str = "data/deepdive.sqlite3"
    environment: str = "development"
    deepdive_api_key: str = ""
    max_research_rounds: int = Field(default=2, ge=0, le=4)
    max_subquestions: int = Field(default=5, ge=1, le=10)
    max_search_results_per_query: int = Field(default=5, ge=1, le=10)
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    max_concurrent_research_runs: int = Field(default=2, ge=1, le=16)
    rate_limit_requests: int = Field(default=10, ge=1, le=1000)
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("environment")
    @classmethod
    def normalize_environment(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"development", "test", "staging", "production"}:
            raise ValueError("environment must be development, test, staging, or production")
        return value

    @property
    def auth_required(self) -> bool:
        # Local learning remains convenient; staging/production fail closed.
        return self.environment in {"staging", "production"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
