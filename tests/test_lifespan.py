import asyncio
from unittest.mock import MagicMock, patch

import pytest

from fastapi import FastAPI


def test_lifespan_initializes_store():
    """store.initialize() must be called before any repo.load() in lifespan."""
    import app.main as main_module

    call_order: list[str] = []
    mock_store = MagicMock()
    mock_store.initialize.side_effect = lambda: call_order.append("initialize")
    mock_repo = MagicMock()
    mock_repo.load.side_effect = lambda m: call_order.append(f"load_{m}")

    async def run():
        with patch.object(main_module, "store", mock_store), patch.object(
            main_module, "repo", mock_repo
        ):
            async with main_module.lifespan(FastAPI()):
                pass

    asyncio.run(run())
    assert "initialize" in call_order
    assert call_order.index("initialize") < min(
        i for i, v in enumerate(call_order) if v.startswith("load_")
    ), "store.initialize() must be called before any repo.load()"


def test_lifespan_closes_store_on_shutdown():
    """store.close() must be called in the lifespan finally block."""
    import app.main as main_module

    mock_store = MagicMock()
    mock_repo = MagicMock()

    async def run():
        with patch.object(main_module, "store", mock_store), patch.object(
            main_module, "repo", mock_repo
        ):
            async with main_module.lifespan(FastAPI()):
                pass

    asyncio.run(run())
    mock_store.close.assert_called_once()
