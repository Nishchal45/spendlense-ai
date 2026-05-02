from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    budgets,
    expenses,
    health,
    inbound,
    insights,
    integrations_gmail,
    receipts,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(expenses.router)
api_router.include_router(receipts.router)
api_router.include_router(insights.router)
api_router.include_router(budgets.router)
api_router.include_router(inbound.router)
api_router.include_router(integrations_gmail.router)
