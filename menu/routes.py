"""API router for the menu application."""
from fastapi import APIRouter

from menu.api_routers.routes import router

api_router = APIRouter()

api_router.include_router(router)
