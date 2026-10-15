from fastapi import APIRouter

from scout_api.modules.auth.router import router as auth_router
from scout_api.modules.crawler.router import router as crawler_router
from scout_api.modules.exchange.router import router as exchange_router
from scout_api.modules.health.router import router as health_router
from scout_api.modules.images.router import router as images_router
from scout_api.modules.matching.router import router as matching_router
from scout_api.modules.monitoring.router import router as monitoring_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(crawler_router)
api_router.include_router(matching_router)
api_router.include_router(images_router)
api_router.include_router(monitoring_router)
api_router.include_router(exchange_router)
