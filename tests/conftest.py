import pandas as pd
import pytest

from app.database.sqlite_store import SQLiteStore


@pytest.fixture
def store(tmp_path):
    s = SQLiteStore(db_path=tmp_path / "test.db")
    s.initialize()
    yield s
    s.close()


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
