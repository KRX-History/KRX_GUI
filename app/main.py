import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.markets import router as markets_router
from app.repository import MARKET_CODES, repo, store

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.initialize()
    try:
        for market in MARKET_CODES:
            try:
                repo.load(market)
            except Exception as exc:
                logger.warning("초기 로딩 실패 [%s]: %s", market, exc)
        yield
    finally:
        store.close()


app = FastAPI(
    title="KRX Market API",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(markets_router)
