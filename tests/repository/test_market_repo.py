from datetime import datetime
from unittest.mock import patch

import pandas as pd
import pytest

from app.database.sqlite_store import SQLiteStore
from app.repository.market_repo import (
    MarketRepository,
    RAW_COLUMNS,
    _next_start,
    _prefilter_with_set,
)


@pytest.fixture
def store(tmp_path):
    s = SQLiteStore(db_path=tmp_path / "test.db")
    s.initialize()
    yield s
    s.close()


@pytest.fixture
def repo(store):
    return MarketRepository(store=store)


@pytest.fixture
def sample_df():
    return pd.DataFrame(
        {
            "시가": [100.0, 101.0],
            "고가": [105.0, 106.0],
            "저가": [99.0, 100.0],
            "종가": [103.0, 104.0],
            "거래량": [1000.0, 1100.0],
            "거래대금": [103000.0, 114400.0],
            "상장시가총액": [1e12, 1.1e12],
        },
        index=pd.to_datetime(["2025-01-02", "2025-01-03"]),
    )


def test_load_from_db_loads_into_memory(repo, store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    repo.load_from_db("KOSPI")
    assert repo.is_loaded("KOSPI")
    df = repo.get("KOSPI")
    assert len(df) == 2


def test_load_from_db_adds_derived_columns(repo, store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    repo.load_from_db("KOSPI")
    df = repo.get("KOSPI")
    assert "등락률" in df.columns
    assert "등락폭" in df.columns


def test_load_from_db_empty_db_does_not_set_loaded(repo):
    repo.load_from_db("KOSPI")
    assert not repo.is_loaded("KOSPI")


def test_load_from_db_empty_db_does_not_insert_key(repo):
    repo.load_from_db("KOSPI")
    assert "KOSPI" not in repo._data


def test_load_from_db_second_call_overwrites_data(repo, store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    repo.load_from_db("KOSPI")
    first_id = id(repo.get("KOSPI"))
    repo.load_from_db("KOSPI")
    second_id = id(repo.get("KOSPI"))
    assert second_id != first_id


# ── Task 7: load() rewrite tests ──────────────────────────────────────────────


def test_next_start_returns_1980_when_no_checkpoint():
    assert _next_start(None) == datetime(1980, 1, 1)


def test_next_start_advances_one_day():
    assert _next_start("2025-01-03") == datetime(2025, 1, 4)


def test_prefilter_removes_existing_dates(sample_df):
    existing = {"2025-01-02"}
    result = _prefilter_with_set(sample_df, existing)
    dates = result.index.strftime("%Y-%m-%d").tolist()
    assert "2025-01-02" not in dates
    assert "2025-01-03" in dates


def test_load_fetches_from_checkpoint(repo, store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    with patch("app.repository.market_repo._fetch_from_pykrx", return_value=pd.DataFrame()) as mock_fetch:
        repo.load("KOSPI")
    call_args = mock_fetch.call_args
    assert call_args[0][1] == "2025-01-04"  # start_date is checkpoint + 1 day


def test_load_prefilter_skips_existing_dates(repo, store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    new_row = pd.DataFrame(
        {
            "시가": [102.0], "고가": [107.0], "저가": [101.0], "종가": [105.0],
            "거래량": [1200.0], "거래대금": [126000.0], "상장시가총액": [1.2e12],
        },
        index=pd.to_datetime(["2025-01-06"]),
    )
    with patch("app.repository.market_repo._fetch_from_pykrx", return_value=new_row):
        repo.load("KOSPI")
    df = store.load_market("KOSPI")
    assert len(df) == 3  # 2 existing + 1 new


def test_load_break_on_fetch_error_preserves_checkpoint(repo, store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    with patch("app.repository.market_repo._fetch_from_pykrx", side_effect=ConnectionError("KRX down")):
        repo.load("KOSPI")  # must not propagate exception
    assert store.get_checkpoint("KOSPI") == "2025-01-03"


def test_load_updates_memory(repo, store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    with patch("app.repository.market_repo._fetch_from_pykrx", return_value=pd.DataFrame()):
        repo.load("KOSPI")
    assert repo.is_loaded("KOSPI")
    assert len(repo.get("KOSPI")) == 2
