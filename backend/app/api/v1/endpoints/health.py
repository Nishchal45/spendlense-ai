import structlog
from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app import __version__
from app.core.database import get_session
from app.core.redis import get_redis
from app.schemas.health import ComponentStatus, HealthResponse

router = APIRouter(tags=["health"])
log = structlog.get_logger()


@router.get("/health", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        components=[ComponentStatus(name="api", status="ok")],
    )


@router.get("/health/ready", response_model=HealthResponse)
async def readiness(response: Response) -> HealthResponse:
    components: list[ComponentStatus] = [ComponentStatus(name="api", status="ok")]

    db_status, db_detail = await _check_database()
    components.append(ComponentStatus(name="database", status=db_status, detail=db_detail))

    redis_status, redis_detail = await _check_redis()
    components.append(ComponentStatus(name="redis", status=redis_status, detail=redis_detail))

    overall = "ok" if all(c.status == "ok" for c in components) else "degraded"
    if overall != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(status=overall, version=__version__, components=components)


async def _check_database() -> tuple[str, str | None]:
    try:
        gen = get_session()
        session = await gen.__anext__()
        try:
            await session.execute(text("SELECT 1"))
        finally:
            await gen.aclose()
    except Exception as exc:  # noqa: BLE001 — boundary catch, logged and mapped
        log.warning("health.database_probe_failed", error=str(exc))
        return "down", str(exc)
    return "ok", None


async def _check_redis() -> tuple[str, str | None]:
    try:
        client = get_redis()
        await client.ping()
    except Exception as exc:  # noqa: BLE001
        log.warning("health.redis_probe_failed", error=str(exc))
        return "down", str(exc)
    return "ok", None
