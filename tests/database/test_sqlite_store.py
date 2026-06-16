import math
import sqlite3

import pandas as pd
import pytest

from app.database.sqlite_store import SQLiteStore, _df_to_rows, RAW_COLUMNS


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


# ── initialize() tests ─────────────────────────────────────────────────────────


def _get_table_info(conn: sqlite3.Connection, table: str) -> list[dict]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    keys = ["cid", "name", "type", "notnull", "dflt_value", "pk"]
    return [dict(zip(keys, row)) for row in rows]


@pytest.fixture
def raw_store(tmp_path):
    """SQLiteStore that has NOT been initialized — caller controls initialize()."""
    s = SQLiteStore(db_path=tmp_path / "init_test.db")
    yield s
    s.close()


# Group A: Connection state

def test_initialize_sets_connection(raw_store):
    assert raw_store._conn is None
    raw_store.initialize()
    assert raw_store._conn is not None


# Group B: PRAGMA settings

def test_initialize_enables_wal_mode(raw_store):
    raw_store.initialize()
    assert raw_store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_initialize_sets_synchronous_normal(raw_store):
    raw_store.initialize()
    assert raw_store._conn.execute("PRAGMA synchronous").fetchone()[0] == 1


# Group C: Schema correctness

def test_initialize_creates_market_data_table(raw_store):
    raw_store.initialize()
    row = raw_store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='market_data'"
    ).fetchone()
    assert row is not None


def test_initialize_market_data_columns_and_not_null(raw_store):
    raw_store.initialize()
    cols = _get_table_info(raw_store._conn, "market_data")
    names = [c["name"] for c in cols]
    assert "market" in names and "date" in names
    market_col = next(c for c in cols if c["name"] == "market")
    date_col = next(c for c in cols if c["name"] == "date")
    assert market_col["notnull"] == 1
    assert date_col["notnull"] == 1


def test_initialize_creates_fetch_checkpoints_table(raw_store):
    raw_store.initialize()
    row = raw_store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='fetch_checkpoints'"
    ).fetchone()
    assert row is not None


def test_initialize_fetch_checkpoints_columns(raw_store):
    raw_store.initialize()
    cols = _get_table_info(raw_store._conn, "fetch_checkpoints")
    names = [c["name"] for c in cols]
    assert "market" in names
    assert "last_success_date" in names
    assert "updated_at" in names


def test_initialize_creates_date_index(raw_store):
    raw_store.initialize()
    row = raw_store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_market_data_date'"
    ).fetchone()
    assert row is not None


def test_initialize_index_ddl_has_date_desc(raw_store):
    raw_store.initialize()
    row = raw_store._conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_market_data_date'"
    ).fetchone()
    assert row is not None
    assert "date DESC" in row[0]


# Group D: Lifecycle / idempotency

def test_initialize_schema_persists_after_reopen(tmp_path):
    db = tmp_path / "persist_test.db"
    s1 = SQLiteStore(db_path=db)
    s1.initialize()
    s1.close()

    s2 = SQLiteStore(db_path=db)
    s2.initialize()
    row = s2._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='market_data'"
    ).fetchone()
    assert row is not None
    s2.close()


def test_initialize_second_call_does_not_raise(raw_store):
    raw_store.initialize()
    assert raw_store._conn is not None  # guard: first call must have opened connection
    raw_store.initialize()              # second call must not raise


def test_initialize_idempotent_tables(raw_store):
    raw_store.initialize()
    tables_before = {
        r[0] for r in raw_store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    raw_store.initialize()
    tables_after = {
        r[0] for r in raw_store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert tables_before == tables_after


# ── upsert_chunk() tests ───────────────────────────────────────────────────────


def test_upsert_chunk_inserts_rows(store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    conn = sqlite3.connect(str(store._db_path))
    count = conn.execute("SELECT COUNT(*) FROM market_data WHERE market='KOSPI'").fetchone()[0]
    conn.close()
    assert count == 2


def test_upsert_chunk_no_duplicate_on_repeat(store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    conn = sqlite3.connect(str(store._db_path))
    count = conn.execute("SELECT COUNT(*) FROM market_data WHERE market='KOSPI'").fetchone()[0]
    conn.close()
    assert count == 2


def test_upsert_chunk_updates_checkpoint(store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    conn = sqlite3.connect(str(store._db_path))
    date = conn.execute(
        "SELECT last_success_date FROM fetch_checkpoints WHERE market='KOSPI'"
    ).fetchone()[0]
    conn.close()
    assert date == "2025-01-03"


def test_upsert_chunk_none_checkpoint_skips_checkpoint_update(store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, checkpoint_date=None)
    conn = sqlite3.connect(str(store._db_path))
    row = conn.execute(
        "SELECT last_success_date FROM fetch_checkpoints WHERE market='KOSPI'"
    ).fetchone()
    conn.close()
    assert row is None


def test_upsert_chunk_empty_df_with_checkpoint(store):
    empty = pd.DataFrame(columns=RAW_COLUMNS)
    empty.index = pd.to_datetime([])
    store.upsert_chunk("KOSPI", empty, "2025-01-03")
    conn = sqlite3.connect(str(store._db_path))
    date = conn.execute(
        "SELECT last_success_date FROM fetch_checkpoints WHERE market='KOSPI'"
    ).fetchone()[0]
    conn.close()
    assert date == "2025-01-03"


def test_upsert_chunk_overwrites_changed_values(store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    updated = sample_df.copy()
    updated["종가"] = [999.0, 888.0]
    store.upsert_chunk("KOSPI", updated, "2025-01-03")
    conn = sqlite3.connect(str(store._db_path))
    rows = conn.execute(
        "SELECT 종가 FROM market_data WHERE market='KOSPI' ORDER BY date"
    ).fetchall()
    conn.close()
    assert rows[0][0] == 999.0
    assert rows[1][0] == 888.0


def test_upsert_chunk_separate_markets_independent(store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    store.upsert_chunk("KOSDAQ", sample_df, "2025-01-03")
    conn = sqlite3.connect(str(store._db_path))
    kospi_count = conn.execute(
        "SELECT COUNT(*) FROM market_data WHERE market='KOSPI'"
    ).fetchone()[0]
    kosdaq_count = conn.execute(
        "SELECT COUNT(*) FROM market_data WHERE market='KOSDAQ'"
    ).fetchone()[0]
    conn.close()
    assert kospi_count == 2
    assert kosdaq_count == 2


# ── read method tests (Task 5) ─────────────────────────────────────────────────


def test_load_market_returns_dataframe(store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    df = store.load_market("KOSPI")
    assert len(df) == 2
    assert list(df.columns) == RAW_COLUMNS
    assert str(df.index.dtype) == "datetime64[ns]"


def test_load_market_empty_returns_empty_df(store):
    df = store.load_market("KOSPI")
    assert df.empty


def test_load_market_null_preserved_as_nan(store):
    df_with_null = pd.DataFrame(
        {
            "시가": [float("nan")],
            "고가": [100.0],
            "저가": [90.0],
            "종가": [95.0],
            "거래량": [500.0],
            "거래대금": [47500.0],
            "상장시가총액": [1e11],
        },
        index=pd.to_datetime(["2025-01-02"]),
    )
    store.upsert_chunk("KOSPI", df_with_null)
    df = store.load_market("KOSPI")
    assert math.isnan(df.iloc[0]["시가"])


def test_get_checkpoint_returns_none_if_missing(store):
    assert store.get_checkpoint("KOSPI") is None


def test_get_checkpoint_returns_date(store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    assert store.get_checkpoint("KOSPI") == "2025-01-03"


def test_get_all_dates_returns_set(store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    dates = store.get_all_dates("KOSPI")
    assert dates == {"2025-01-02", "2025-01-03"}


def test_markets_in_db_returns_all_markets(store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    store.upsert_chunk("KOSDAQ", sample_df, "2025-01-03")
    assert set(store.markets_in_db()) == {"KOSPI", "KOSDAQ"}
