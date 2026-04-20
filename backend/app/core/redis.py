from redis.asyncio import Redis, from_url

from app.core.config import Settings, get_settings

_client: Redis | None = None


def init_redis(settings: Settings | None = None) -> Redis:
    global _client

    if _client is not None:
        return _client

    settings = settings or get_settings()
    _client = from_url(
        str(settings.redis_url),
        encoding="utf-8",
        decode_responses=True,
    )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


def get_redis() -> Redis:
    if _client is None:
        init_redis()
    assert _client is not None
    return _client
