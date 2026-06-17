import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.markets import router as markets_router
from app.repository import MARKET_CODES, repo, store
from app.repository.market_repo import CSV_PATH
from app.watchers.csv_watcher import watch_csv

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. SQLite 초기화 (WAL + 테이블 생성)
    store.initialize()

    # 2. SQLite → 인메모리 즉시 복구 (pyKrx 없이 서비스 가능)
    for market in MARKET_CODES:
        try:
            repo.load_from_db(market)
        except Exception as exc:
            logger.warning("SQLite 복구 실패 [%s]: %s", market, exc)

    # 3. pyKrx 증분 갱신 — 예외 로깅 포함 task wrapper
    async def _bg_load(market: str) -> None:
        try:
            await asyncio.get_running_loop().run_in_executor(None, repo.load, market)
        except Exception:
            logger.exception("pyKrx 증분 갱신 실패 [%s]", market)

    load_tasks = [asyncio.create_task(_bg_load(m)) for m in MARKET_CODES]

    # 4. CSV 감시 시작
    csv_task = asyncio.create_task(watch_csv(CSV_PATH, repo.ingest_csv))

    yield

    csv_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await csv_task
    await asyncio.gather(*load_tasks, return_exceptions=True)
    store.close()


app = FastAPI(
    title="KRX Market API",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(markets_router)
