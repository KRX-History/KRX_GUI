import pandas as pd
import pytest

from app.database.sqlite_store import SQLiteStore
from app.repository.market_repo import MarketRepository, RAW_COLUMNS


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
