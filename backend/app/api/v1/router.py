from fastapi import APIRouter

from app.api.v1.endpoints import auth, expenses, health, receipts

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(expenses.router)
api_router.include_router(receipts.router)
