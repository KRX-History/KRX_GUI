import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from watchfiles import awatch

logger = logging.getLogger(__name__)


async def watch_csv(csv_path: Path, on_change: Callable[[], None]) -> None:
    async for _ in awatch(csv_path):
        logger.info("CSV 변경 감지: %s", csv_path)
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, on_change)
            logger.info("CSV ingest 완료")
        except Exception as exc:
            logger.error("CSV ingest 실패: %s", exc)
