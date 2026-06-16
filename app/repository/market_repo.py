import logging
import threading
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

import pandas as pd

from app.database.sqlite_store import SQLiteStore, RAW_COLUMNS as _DB_RAW_COLUMNS, store as _default_store

logger = logging.getLogger(__name__)

# 프로젝트 루트 기준 data/ 디렉토리
CSV_PATH = Path(__file__).parent.parent.parent / "data" / "test.csv"

MARKET_CODES = {
    "KOSPI":  "1001",
    "KOSDAQ": "2001",
}

RAW_COLUMNS = ["시가", "고가", "저가", "종가", "거래량", "거래대금", "상장시가총액"]

# 거래량은 파생 컬럼 계산에 불필요 — incremental concat 시 기준 컬럼
_BASE_COLS = ["시가", "고가", "저가", "종가", "거래대금", "상장시가총액"]

# 불일치 검사 대상 컬럼 — 종가 단일에서 OHLC 전체로 확장
_CHECK_COLS = ["시가", "고가", "저가", "종가"]


class ConflictResolution(Enum):
    PREFER_PYKRX = "pykrx"
    PREFER_CSV   = "csv"
    RAISE_ERROR  = "error"


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
    # 누락 컬럼은 NaN으로 채움 — 0은 유효한 값처럼 보여 통계를 오염시킴
    return df.reindex(columns=RAW_COLUMNS)


def _merge_sources(
    df_primary: pd.DataFrame,
    df_secondary: pd.DataFrame,
    conflict: ConflictResolution = ConflictResolution.PREFER_PYKRX,
    tolerance: float = 0.01,
) -> pd.DataFrame:
    overlap = df_primary.index.intersection(df_secondary.index)

    if len(overlap) > 0:
        # 원본 참조로 불일치 검사 — copy 불필요
        # replace(0, NaN): secondary 종가가 0이면 division by zero → inf 전파 방지
        denom    = df_secondary.loc[overlap, _CHECK_COLS].replace(0, float("nan"))
        diff_pct = ((df_primary.loc[overlap, _CHECK_COLS] - denom) / denom).abs()

        # 날짜별로 어느 컬럼이라도 tolerance 초과하면 불일치로 판정
        conflict_mask = diff_pct.max(axis=1) > tolerance
        if conflict_mask.any():
            bad_dates = conflict_mask[conflict_mask].index.tolist()
            bad_cols  = diff_pct.loc[conflict_mask].columns[
                diff_pct.loc[conflict_mask].gt(tolerance).any()
            ].tolist()
            msg = f"데이터 불일치 {conflict_mask.sum()}건 (날짜: {bad_dates}, 컬럼: {bad_cols})"
            if conflict == ConflictResolution.RAISE_ERROR:
                raise ValueError(msg)
            logger.warning("%s — '%s' 우선 적용", msg, conflict.value)

        # drop() 대신 boolean mask — 패배 소스의 view만 선택, copy 없음
        if conflict == ConflictResolution.PREFER_PYKRX:
            merged = pd.concat([df_primary, df_secondary[~df_secondary.index.isin(overlap)]])
        else:
            merged = pd.concat([df_primary[~df_primary.index.isin(overlap)], df_secondary])
    else:
        merged = pd.concat([df_primary, df_secondary])

    merged = merged.sort_index()

    if merged.index.duplicated().any():
        raise RuntimeError("병합 후 중복 인덱스 존재 — 로직 오류")
    if not merged.index.is_monotonic_increasing:
        raise RuntimeError("날짜 순서가 깨졌습니다")

    logger.info("병합 완료: 총 %d 거래일 (겹침 %d일 해소)", len(merged), len(overlap))
    return merged


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

    def load_from_db(self, market: str) -> None:
        df = self._store.load_market(market)
        if df.empty:
            return
        fresh = _add_derived_columns(df)
        with self._lock:
            self._data[market] = fresh
        logger.info("[%s] SQLite 복구 완료: %d행", market, len(fresh))

    def load(self, market: str) -> None:
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
                if chunk.empty:
                    break
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
        if market not in self._data:
            raise KeyError(f"'{market}' 데이터가 적재되지 않았습니다.")
        return self._data[market]  # copy 책임은 실제로 변형이 필요한 서비스 레이어로 이동

    def is_loaded(self, market: str) -> bool:
        return market in self._data


# 앱 전역 단일 인스턴스
repo = MarketRepository()
