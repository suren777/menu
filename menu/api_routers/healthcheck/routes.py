from fastapi import APIRouter 
router = APIRouter()


@router.get("/healthcheck",  tags=["healthcheck"])
async def healthcheck( ) -> dict[str, str]:
    return {
        "service": 'menu',
        "environment": 'dev',
    }
