import math
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import pandas as pd

RAW_COLUMNS = ["시가", "고가", "저가", "종가", "거래량", "거래대금", "상장시가총액"]

DB_PATH = Path(__file__).parent.parent.parent / "krx_market.db"

_CREATE_MARKET_DATA = """
CREATE TABLE IF NOT EXISTS market_data (
    market         TEXT NOT NULL,
    date           TEXT NOT NULL,
    시가           REAL,
    고가           REAL,
    저가           REAL,
    종가           REAL,
    거래량         REAL,
    거래대금       REAL,
    상장시가총액   REAL,
    PRIMARY KEY (market, date)
) WITHOUT ROWID
"""

_CREATE_CHECKPOINTS = """
CREATE TABLE IF NOT EXISTS fetch_checkpoints (
    market            TEXT PRIMARY KEY,
    last_success_date TEXT NOT NULL,
    updated_at        TEXT NOT NULL
) WITHOUT ROWID
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_market_data_date
    ON market_data (market, date DESC)
"""

_UPSERT_SQL = """
INSERT INTO market_data (market, date, 시가, 고가, 저가, 종가, 거래량, 거래대금, 상장시가총액)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(market, date) DO UPDATE SET
    시가         = excluded.시가,
    고가         = excluded.고가,
    저가         = excluded.저가,
    종가         = excluded.종가,
    거래량       = excluded.거래량,
    거래대금     = excluded.거래대금,
    상장시가총액 = excluded.상장시가총액
WHERE
    excluded.종가     IS NOT market_data.종가
    OR excluded.시가  IS NOT market_data.시가
    OR excluded.고가  IS NOT market_data.고가
    OR excluded.저가  IS NOT market_data.저가
"""

_UPSERT_CHECKPOINT = """
INSERT OR REPLACE INTO fetch_checkpoints (market, last_success_date, updated_at)
VALUES (?, ?, ?)
"""

_SELECT_MARKET = """
SELECT date, 시가, 고가, 저가, 종가, 거래량, 거래대금, 상장시가총액
FROM market_data WHERE market = ? ORDER BY date
"""

_SELECT_CHECKPOINT = "SELECT last_success_date FROM fetch_checkpoints WHERE market = ?"

_SELECT_ALL_DATES = "SELECT date FROM market_data WHERE market = ?"

_SELECT_MARKETS = "SELECT DISTINCT market FROM market_data"


def _df_to_rows(market: str, df: pd.DataFrame) -> list[tuple]:
    clean = (
        df[RAW_COLUMNS]
        .replace([math.inf, -math.inf], float("nan"))
        .to_numpy(dtype=object, na_value=None)
        .tolist()
    )
    dates = df.index.strftime("%Y-%m-%d").tolist()
    return [(market, date, *row) for date, row in zip(dates, clean)]


class SQLiteStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._write_lock = threading.Lock()

    def initialize(self) -> None:
        pass  # Task 3에서 구현

    def upsert_chunk(self, market: str, df: pd.DataFrame, checkpoint_date: str | None = None) -> None:
        raise NotImplementedError

    def load_market(self, market: str) -> pd.DataFrame:
        raise NotImplementedError

    def get_checkpoint(self, market: str) -> str | None:
        raise NotImplementedError

    def get_all_dates(self, market: str) -> set[str]:
        raise NotImplementedError

    def markets_in_db(self) -> list[str]:
        raise NotImplementedError

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


store = SQLiteStore()
