from datetime import datetime
from pathlib import Path
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
    # Check first call's start_date (not last) — loop may make multiple calls per year
    assert mock_fetch.call_args_list[0][0][1] == "2025-01-04"


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


# ── H1: empty year must not abort subsequent years ────────────────────────────


def test_load_continues_after_empty_year(repo, store):
    """Empty 1980 must not abort the loop — 1981 data must still be stored."""
    year_data = pd.DataFrame(
        {
            "시가": [100.0], "고가": [105.0], "저가": [99.0], "종가": [103.0],
            "거래량": [1000.0], "거래대금": [103000.0], "상장시가총액": [1e12],
        },
        index=pd.to_datetime(["1981-01-02"]),
    )

    def _fake_fetch(code, start_date, end_date):
        if start_date.startswith("1981"):
            return year_data
        return pd.DataFrame()

    with patch("app.repository.market_repo._fetch_from_pykrx", side_effect=_fake_fetch):
        repo.load("KOSPI")

    df = store.load_market("KOSPI")
    assert len(df) == 1  # 1981 data was stored despite empty 1980


# ── Fix B: RAW_COLUMNS 단일 소스화 ─────────────────────────────────────────────


def test_raw_columns_single_source():
    """RAW_COLUMNS는 sqlite_store에서 단일 정의되어야 한다 (같은 객체)."""
    from app.database.sqlite_store import RAW_COLUMNS as db_cols
    from app.repository.market_repo import RAW_COLUMNS as repo_cols

    assert db_cols is repo_cols


# ── Fix C: 죽은 코드 삭제 ──────────────────────────────────────────────────────


import app.repository.market_repo as _mr  # noqa: E402


def test_dead_code_removed():
    """삭제된 심볼이 모듈에 없어야 한다."""
    assert not hasattr(_mr, "_merge_sources")
    assert not hasattr(_mr, "ConflictResolution")
    assert not hasattr(_mr, "_BASE_COLS")


# ── Fix D: 빈 연도 건너뛰기 (회귀 테스트) ────────────────────────────────────


def test_load_continues_past_empty_year(repo, store, sample_df):
    """빈 연도를 만나도 루프가 종료되지 않고 현재 연도까지 fetch해야 한다."""
    store.upsert_chunk("KOSPI", sample_df, "2024-12-31")

    fetch_call_years: list[str] = []

    def fake_fetch(code, start_date, end_date):
        year = start_date[:4]
        fetch_call_years.append(year)
        if year == "2025":
            return pd.DataFrame(columns=RAW_COLUMNS)
        return sample_df

    with patch("app.repository.market_repo._fetch_from_pykrx", side_effect=fake_fetch):
        repo.load("KOSPI")

    assert "2025" in fetch_call_years
    assert "2026" in fetch_call_years, "2025이 비어도 2026 fetch가 이어져야 함"


# ── Task 8: ingest_csv() ───────────────────────────────────────────────────────


def test_ingest_csv_loads_data(repo, store, tmp_path):
    csv_path = tmp_path / "test.csv"
    csv_path.write_text(
        "날짜,시가,고가,저가,종가,거래량,거래대금,상장시가총액\n"
        "2025-06-02,2620.45,2638.90,2611.23,2630.17,412380000,8234500000000,2187650000000000\n"
    )
    with patch("app.repository.market_repo.CSV_PATH", csv_path):
        repo.ingest_csv()
    assert repo.is_loaded("KOSPI")
    assert len(repo.get("KOSPI")) == 1


def test_ingest_csv_deduplicates(repo, store, tmp_path):
    csv_path = tmp_path / "test.csv"
    row = "2025-06-02,2620.45,2638.90,2611.23,2630.17,412380000,8234500000000,2187650000000000\n"
    csv_path.write_text("날짜,시가,고가,저가,종가,거래량,거래대금,상장시가총액\n" + row)
    with patch("app.repository.market_repo.CSV_PATH", csv_path):
        repo.ingest_csv()
        repo.ingest_csv()
    assert len(repo.get("KOSPI")) == 1


def test_ingest_csv_missing_file_does_not_raise(repo):
    with patch("app.repository.market_repo.CSV_PATH", Path("/nonexistent/path.csv")):
        repo.ingest_csv()
