from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False

    api_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    database_url: PostgresDsn
    database_pool_size: int = 10
    database_max_overflow: int = 5

    redis_url: RedisDsn

    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    jwt_access_token_ttl_minutes: int = 60 * 24

    s3_endpoint_url: str | None = None
    s3_bucket: str = "receipts"
    s3_access_key: str
    s3_secret_key: str
    s3_region: str = "us-east-1"

    openai_api_key: str | None = None
    openai_model_categorise: str = "gpt-4o-mini"
    openai_model_vision: str = "gpt-4o"

    ocr_confidence_threshold: float = 60.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
