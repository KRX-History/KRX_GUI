from unittest.mock import MagicMock, patch

import pytest

from app.watchers.csv_watcher import watch_csv


@pytest.fixture
def on_change():
    return MagicMock()


@pytest.mark.asyncio
async def test_watch_csv_calls_on_change_on_event(tmp_path, on_change):
    csv_path = tmp_path / "test.csv"

    async def fake_awatch(path):
        yield {(1, str(path))}

    with patch("app.watchers.csv_watcher.awatch", fake_awatch):
        await watch_csv(csv_path, on_change)

    on_change.assert_called_once()


@pytest.mark.asyncio
async def test_watch_csv_continues_after_error(tmp_path):
    call_count = 0

    async def fake_awatch(path):
        yield {(1, str(path))}
        yield {(1, str(path))}

    def failing_callback():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("ingest 실패")

    with patch("app.watchers.csv_watcher.awatch", fake_awatch):
        await watch_csv(tmp_path / "t.csv", failing_callback)

    assert call_count == 2
