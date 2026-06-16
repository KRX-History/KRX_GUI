import logging
import threading
from datetime import datetime
from enum import Enum
from pathlib import Path

import pandas as pd
from pykrx import stock

logger = logging.getLogger(__name__)

# 프로젝트 루트 기준 data/ 디렉토리
CSV_PATH = Path(__file__).parent.parent.parent / "data" / "test.csv"

MARKET_CODES = {
    "KOSPI":  "1001",
    "KOSDAQ": "2001",
}

RAW_COLUMNS = ["시가", "고가", "저가", "종가", "거래량", "거래대금", "상장시가총액"]


class ConflictResolution(Enum):
    PREFER_PYKRX = "pykrx"
    PREFER_CSV   = "csv"
    RAISE_ERROR  = "error"


def _fetch_from_pykrx(market_code: str) -> pd.DataFrame:
    today = datetime.today().strftime("%Y-%m-%d")
    df = stock.get_index_ohlcv("1980-01-01", today, market_code)
    df.index = pd.to_datetime(df.index)
    return df[RAW_COLUMNS]


def _load_from_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["날짜"], index_col="날짜")
    df.index = pd.to_datetime(df.index)
    for col in RAW_COLUMNS:
        if col not in df.columns:
            df[col] = 0
    return df[RAW_COLUMNS]


def _merge_sources(
    df_primary: pd.DataFrame,
    df_secondary: pd.DataFrame,
    conflict: ConflictResolution = ConflictResolution.PREFER_PYKRX,
    tolerance: float = 0.01,
) -> pd.DataFrame:
    primary   = df_primary.copy()
    secondary = df_secondary.copy()

    overlap = primary.index.intersection(secondary.index)

    if len(overlap) > 0:
        diff_pct = (
            (primary.loc[overlap, "종가"] - secondary.loc[overlap, "종가"])
            / secondary.loc[overlap, "종가"]
        ).abs()
        conflicts = diff_pct[diff_pct > tolerance]

        if not conflicts.empty:
            msg = f"데이터 불일치 {len(conflicts)}건: {conflicts.index.tolist()}"
            if conflict == ConflictResolution.RAISE_ERROR:
                raise ValueError(msg)
            logger.warning("%s — '%s' 우선 적용", msg, conflict.value)

        if conflict == ConflictResolution.PREFER_PYKRX:
            secondary = secondary.drop(index=overlap)
        else:
            primary = primary.drop(index=overlap)

    merged = pd.concat([primary, secondary]).sort_index()

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
    def __init__(self) -> None:
        self._data: dict[str, pd.DataFrame] = {}
        self._lock = threading.Lock()

    def load(
        self,
        market: str,
        csv_path: Path | None = CSV_PATH,
        conflict: ConflictResolution = ConflictResolution.PREFER_PYKRX,
    ) -> None:
        # 비싼 I/O와 계산은 락 밖에서 수행 — 공유 상태를 건드리지 않음
        try:
            code     = MARKET_CODES[market]
            df_pykrx = _fetch_from_pykrx(code)

            if csv_path and csv_path.exists():
                df_csv    = _load_from_csv(csv_path)
                df_merged = _merge_sources(df_pykrx, df_csv, conflict)
            else:
                logger.info("CSV 없음 — pykrx 단독 사용")
                df_merged = df_pykrx

            new_data = _add_derived_columns(df_merged)
        except Exception as exc:
            logger.error("[%s] 데이터 처리 실패: %s", market, exc)
            raise

        # 계산 완료 후 락 안에서 원자적 교체 — 실패해도 이전 데이터 유지
        with self._lock:
            self._data[market] = new_data

        logger.info("[%s] 적재 완료: %d행", market, len(new_data))

    def get(self, market: str) -> pd.DataFrame:
        if market not in self._data:
            raise KeyError(f"'{market}' 데이터가 적재되지 않았습니다.")
        return self._data[market].copy()

    def is_loaded(self, market: str) -> bool:
        return market in self._data


# 앱 전역 단일 인스턴스
repo = MarketRepository()
