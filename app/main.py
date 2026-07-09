from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import ingestion, instruments, prices
from app.routers.dashboard import router as dashboard_router
from app.routers.gold_analysis import router as gold_router
from app.scheduler import start_scheduler, stop_scheduler
from app.services.ingestion_service import register_provider
from app.services.providers.yfinance_provider import YFinanceProvider

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    register_provider(YFinanceProvider())
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="MarketLens API", version="0.2.0", lifespan=lifespan)

allowed_origins = [origin.strip() for origin in settings.cors_allowed_origins.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=settings.cors_allowed_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(instruments.router)
app.include_router(prices.router)
app.include_router(ingestion.router)
app.include_router(dashboard_router)
app.include_router(gold_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
