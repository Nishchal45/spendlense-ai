from fastapi import APIRouter

from app import __version__
from app.schemas.health import ComponentStatus, HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        components=[
            ComponentStatus(name="api", status="ok"),
        ],
    )
