import math

import pandas as pd
import pytest

from app.database.sqlite_store import _df_to_rows, RAW_COLUMNS


@pytest.fixture
def sample_df():
    return pd.DataFrame(
        {
            "시가": [100.0, float("nan")],
            "고가": [105.0, 106.0],
            "저가": [99.0, float("inf")],
            "종가": [103.0, 104.0],
            "거래량": [1000.0, 1100.0],
            "거래대금": [103000.0, float("-inf")],
            "상장시가총액": [1e12, None],
        },
        index=pd.to_datetime(["2025-01-02", "2025-01-03"]),
    )


def test_df_to_rows_length(sample_df):
    rows = _df_to_rows("KOSPI", sample_df)
    assert len(rows) == 2


def test_df_to_rows_market_and_date(sample_df):
    rows = _df_to_rows("KOSPI", sample_df)
    assert rows[0][0] == "KOSPI"
    assert rows[0][1] == "2025-01-02"


def test_df_to_rows_nan_becomes_none(sample_df):
    rows = _df_to_rows("KOSPI", sample_df)
    시가_idx = 2 + RAW_COLUMNS.index("시가")
    assert rows[1][시가_idx] is None


def test_df_to_rows_inf_becomes_none(sample_df):
    rows = _df_to_rows("KOSPI", sample_df)
    저가_idx = 2 + RAW_COLUMNS.index("저가")
    거래대금_idx = 2 + RAW_COLUMNS.index("거래대금")
    assert rows[1][저가_idx] is None
    assert rows[1][거래대금_idx] is None


def test_df_to_rows_normal_value_preserved(sample_df):
    rows = _df_to_rows("KOSPI", sample_df)
    종가_idx = 2 + RAW_COLUMNS.index("종가")
    assert rows[0][종가_idx] == 103.0
