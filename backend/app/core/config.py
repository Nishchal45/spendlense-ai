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

    # Domain the user's forward-to-email address lives under. The
    # full address is ``receipts+<inbox_token>@<inbox_email_domain>``.
    # The dev default points at a placeholder so a missing env var
    # doesn't take down ``/auth/me``; production deployments override
    # via ``INBOX_EMAIL_DOMAIN`` to the real MX-pointed host.
    inbox_email_domain: str = "inbox.spendlens.local"
    # Shared secret the inbound-email webhook (Postmark / SES /
    # Mailgun) signs with. Never logged. Optional in dev so the
    # rest of the app boots without it; the inbound endpoint
    # rejects every request with a 503 when it's unset.
    inbound_email_secret: str | None = None

    # ----- Gmail OAuth (Phase 5.6) ----------------------------------
    # OAuth client credentials issued by the Google Cloud project
    # backing the SpendLens production deploy. Both optional in dev
    # so the app boots without Gmail wiring; the integration routes
    # return 503 when either is missing. Never logged.
    gmail_oauth_client_id: str | None = None
    gmail_oauth_client_secret: str | None = None
    # Where Google sends the user back after consent. Must exactly
    # match a redirect URI registered with the OAuth client.
    gmail_oauth_redirect_uri: str = "http://localhost:8000/api/v1/integrations/gmail/callback"
    # Fernet key (URL-safe base64-encoded 32 bytes). Used to encrypt
    # the user's Gmail refresh token at rest. Generate once via
    # ``Fernet.generate_key()`` and pin in ``.env``. Optional in dev
    # so the rest of the app boots; the integration routes 503 on
    # any encrypt / decrypt path when unset.
    gmail_token_encryption_key: str | None = None
    # Audience claim Google Cloud Pub/Sub puts in the JWT it signs
    # every push delivery with. Set to the same string configured on
    # the Pub/Sub subscription (typically the push endpoint URL,
    # e.g. ``https://api.spendlens.example/api/v1/integrations/gmail/push``).
    # The push handler rejects deliveries whose ``aud`` claim doesn't
    # match — that's how we know the request really came from our
    # subscription and not someone replaying a Pub/Sub push from an
    # unrelated project. Optional in dev so the app boots; the push
    # endpoint 503s when unset.
    gmail_pubsub_audience: str | None = None
    # Email address of the service account Google signs Pub/Sub push
    # JWTs with. Set to the service account configured on the push
    # subscription (e.g. ``pubsub-push@<project>.iam.gserviceaccount.com``).
    # The push handler also requires ``iss`` to be Google's well-known
    # OIDC issuer. Optional in dev for the same reason as the audience.
    gmail_pubsub_service_account: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
