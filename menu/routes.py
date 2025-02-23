from fastapi import APIRouter
 
from menu.healthcheck.routes import router as healtcheck_router

api_router = APIRouter()

api_router.include_router(healtcheck_router)