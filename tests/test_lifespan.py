import asyncio
import logging
import time
import threading
from unittest.mock import MagicMock, patch

import pytest

from fastapi import FastAPI


def test_lifespan_initializes_store():
    """store.initialize() must be called before repo.load_from_db() in lifespan."""
    import app.main as main_module

    call_order: list[str] = []
    mock_store = MagicMock()
    mock_store.initialize.side_effect = lambda: call_order.append("initialize")
    mock_repo = MagicMock()
    mock_repo.load_from_db.side_effect = lambda m: call_order.append(f"load_from_db_{m}")

    async def noop_watch(path, callback):
        pass

    async def run():
        with (
            patch.object(main_module, "store", mock_store),
            patch.object(main_module, "repo", mock_repo),
            patch.object(main_module, "watch_csv", noop_watch),
        ):
            async with main_module.lifespan(FastAPI()):
                pass

    asyncio.run(run())
    assert "initialize" in call_order
    assert call_order.index("initialize") < min(
        i for i, v in enumerate(call_order) if v.startswith("load_from_db_")
    ), "store.initialize() must be called before any repo.load_from_db()"


def test_lifespan_closes_store_on_shutdown():
    """store.close() must be called in the lifespan finally block."""
    import app.main as main_module

    mock_store = MagicMock()
    mock_repo = MagicMock()

    async def noop_watch(path, callback):
        pass

    async def run():
        with (
            patch.object(main_module, "store", mock_store),
            patch.object(main_module, "repo", mock_repo),
            patch.object(main_module, "watch_csv", noop_watch),
        ):
            async with main_module.lifespan(FastAPI()):
                pass

    asyncio.run(run())
    mock_store.close.assert_called_once()


# ── Task 10: new lifespan behaviors ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_lifespan_recovers_from_db_on_startup():
    """Step 2: repo.load_from_db must be called for every market before yield."""
    import app.main as main_module

    mock_store = MagicMock()
    mock_repo = MagicMock()

    async def noop_watch(path, callback):
        pass

    with (
        patch.object(main_module, "store", mock_store),
        patch.object(main_module, "repo", mock_repo),
        patch.object(main_module, "watch_csv", noop_watch),
    ):
        async with main_module.lifespan(FastAPI()):
            pass

    assert mock_repo.load_from_db.call_count == len(main_module.MARKET_CODES)


@pytest.mark.asyncio
async def test_lifespan_starts_csv_watcher():
    """Step 4: watch_csv must be started as a task with repo.ingest_csv callback."""
    import app.main as main_module

    mock_store = MagicMock()
    mock_repo = MagicMock()
    watch_calls: list = []

    async def spy_watch(path, callback):
        watch_calls.append((path, callback))

    with (
        patch.object(main_module, "store", mock_store),
        patch.object(main_module, "repo", mock_repo),
        patch.object(main_module, "watch_csv", spy_watch),
    ):
        async with main_module.lifespan(FastAPI()):
            await asyncio.sleep(0)  # give event loop one tick to start the task

    assert len(watch_calls) == 1
    assert watch_calls[0][1] == mock_repo.ingest_csv


@pytest.mark.asyncio
async def test_lifespan_cancels_csv_task_on_shutdown():
    """CSV watcher task must be cancelled when the server shuts down."""
    import app.main as main_module

    mock_store = MagicMock()
    mock_repo = MagicMock()
    was_cancelled = False

    async def long_running_watch(path, callback):
        nonlocal was_cancelled
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            was_cancelled = True
            raise

    with (
        patch.object(main_module, "store", mock_store),
        patch.object(main_module, "repo", mock_repo),
        patch.object(main_module, "watch_csv", long_running_watch),
    ):
        async with main_module.lifespan(FastAPI()):
            await asyncio.sleep(0)  # tick 1: task starts, reaches asyncio.sleep(3600)
        await asyncio.sleep(0)  # tick 2: CancelledError propagated into task

    assert was_cancelled


# ── C1+C2a: executor error logging and shutdown ordering ──────────────────────


@pytest.mark.asyncio
async def test_executor_errors_are_logged(caplog):
    """repo.load 예외가 소실되지 않고 error 로그로 기록되어야 한다."""
    import app.main as main_module

    mock_store = MagicMock()
    mock_repo = MagicMock()
    mock_repo.load.side_effect = RuntimeError("pykrx 연결 실패")

    async def noop_watch(path, callback):
        pass

    with (
        patch.object(main_module, "store", mock_store),
        patch.object(main_module, "repo", mock_repo),
        patch.object(main_module, "watch_csv", noop_watch),
        caplog.at_level(logging.ERROR, logger="app.main"),
    ):
        async with main_module.lifespan(FastAPI()):
            await asyncio.sleep(0.05)  # give executor tasks time to complete

    assert any(
        "pyKrx 증분 갱신 실패" in r.message for r in caplog.records
    ), "executor 예외가 로그에 기록되지 않음 (RED)"


@pytest.mark.asyncio
async def test_shutdown_awaits_load_tasks_before_close():
    """store.close()는 load task가 완료된 후에 호출되어야 한다."""
    import app.main as main_module

    order: list[str] = []

    mock_store = MagicMock()
    mock_store.close.side_effect = lambda: order.append("close")

    mock_repo = MagicMock()

    def slow_load(market):
        time.sleep(0.05)
        order.append(f"load_{market}")

    mock_repo.load.side_effect = slow_load

    async def noop_watch(path, callback):
        pass

    with (
        patch.object(main_module, "store", mock_store),
        patch.object(main_module, "repo", mock_repo),
        patch.object(main_module, "watch_csv", noop_watch),
    ):
        async with main_module.lifespan(FastAPI()):
            pass

    assert all(f"load_{m}" in order for m in main_module.MARKET_CODES), \
        "load task가 실행되지 않음"
    assert "close" in order
    assert all(
        order.index(f"load_{m}") < order.index("close")
        for m in main_module.MARKET_CODES
    ), "store.close()가 load task 완료 전에 호출됨 (RED)"
