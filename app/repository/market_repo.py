import logging
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from app.database.sqlite_store import SQLiteStore, RAW_COLUMNS, store as _default_store

logger = logging.getLogger(__name__)

CSV_PATH = Path(__file__).parent.parent.parent / "data" / "kospi_data.csv"
CSV_MARKET = "KOSPI"

MARKET_CODES = {
    "KOSPI":  "1001",
    "KOSDAQ": "2001",
}


def _fetch_from_pykrx(market_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    from pykrx import stock  # lazy import — avoids pkg_resources at module load time
    df = stock.get_index_ohlcv(start_date, end_date, market_code)
    df.index = pd.to_datetime(df.index)
    return df[RAW_COLUMNS]


def _next_start(checkpoint: str | None) -> datetime:
    if checkpoint is None:
        return datetime(1980, 1, 1)
    return datetime.strptime(checkpoint, "%Y-%m-%d") + timedelta(days=1)


def _prefilter_with_set(df: pd.DataFrame, existing_dates: set[str]) -> pd.DataFrame:
    mask = ~df.index.strftime("%Y-%m-%d").isin(existing_dates)
    return df[mask]


def _load_from_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["날짜"], index_col="날짜")
    df.index = pd.to_datetime(df.index)
    return df.reindex(columns=RAW_COLUMNS)


def _add_derived_columns(data: pd.DataFrame) -> pd.DataFrame:
    # 모든 컬럼을 숫자형으로 유지. 포맷팅은 API 응답 직렬화 시점에만 수행.
    return (
        data
        .assign(
            등락률=lambda d: d["종가"].pct_change() * 100,
            등락폭=lambda d: d["종가"].diff(),
        )
        [["종가", "등락폭", "등락률", "시가", "고가", "저가", "거래대금", "상장시가총액"]]
    )


class MarketRepository:
    def __init__(self, store: SQLiteStore = _default_store) -> None:
        self._store = store
        self._data: dict[str, pd.DataFrame] = {}
        self._lock = threading.Lock()
        self._load_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)

    def load_from_db(self, market: str) -> None:
        df = self._store.load_market(market)
        if df.empty:
            return
        fresh = _add_derived_columns(df)
        with self._lock:
            self._data[market] = fresh
        logger.info("[%s] SQLite 복구 완료: %d행", market, len(fresh))

    def load(self, market: str) -> None:
        with self._load_locks[market]:
            code = MARKET_CODES[market]
            existing_dates = self._store.get_all_dates(market)
            checkpoint = self._store.get_checkpoint(market)
            current = _next_start(checkpoint)
            today = datetime.today()

            while current <= today:
                year_end = min(datetime(current.year, 12, 31), today)
                year_end_str = year_end.strftime("%Y-%m-%d")
                try:
                    chunk = _fetch_from_pykrx(code, current.strftime("%Y-%m-%d"), year_end_str)
                    if not chunk.empty:
                        filtered = _prefilter_with_set(chunk, existing_dates)
                        if not filtered.empty:
                            self._store.upsert_chunk(market, filtered, year_end_str)
                            existing_dates.update(filtered.index.strftime("%Y-%m-%d").tolist())
                except Exception as exc:
                    logger.error("[%s] fetch 실패 (%s): %s — 체크포인트 보존", market, year_end.date(), exc)
                    break
                current = datetime(current.year + 1, 1, 1)

            fresh = _add_derived_columns(self._store.load_market(market))
            with self._lock:
                self._data[market] = fresh
            logger.info("[%s] 적재 완료: %d행", market, len(fresh))

    def get(self, market: str) -> pd.DataFrame:
        with self._lock:
            if market not in self._data:
                raise KeyError(f"'{market}' 데이터가 적재되지 않았습니다.")
            return self._data[market]

    def ingest_csv(self) -> None:
        try:
            df = _load_from_csv(CSV_PATH)
        except (FileNotFoundError, IOError) as exc:
            logger.warning("CSV 파일 읽기 실패: %s", exc)
            return
        existing_dates = self._store.get_all_dates(CSV_MARKET)
        filtered = _prefilter_with_set(df, existing_dates)
        if filtered.empty:
            with self._lock:
                if CSV_MARKET in self._data:
                    return
        else:
            checkpoint = filtered.index.strftime("%Y-%m-%d").max()
            self._store.upsert_chunk(CSV_MARKET, filtered, checkpoint)
        fresh = _add_derived_columns(self._store.load_market(CSV_MARKET))
        with self._lock:
            self._data[CSV_MARKET] = fresh
        logger.info("[%s] CSV 적재 완료: %d행", CSV_MARKET, len(fresh))

    def is_loaded(self, market: str) -> bool:
        with self._lock:
            return market in self._data


# 앱 전역 단일 인스턴스
repo = MarketRepository()
