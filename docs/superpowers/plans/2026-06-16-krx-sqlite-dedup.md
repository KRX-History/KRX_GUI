# KRX SQLite 영속화 & 중복 제거 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SQLite를 진실의 원천으로 도입하여, CSV·pyKrx 데이터의 중복 없는 영속화와 5XX 장애 후 자동 복구를 구현한다.

**Architecture:** `SQLiteStore`가 WAL 모드 SQLite를 관리하며 `ON CONFLICT DO UPDATE` Upsert로 DB 레벨 중복 제거를 수행한다. `MarketRepository`는 시작 시 SQLite에서 즉시 복구하고, 이후 연도별 청크로 pyKrx를 증분 동기화한다. `watchfiles`가 CSV 변경을 감지해 자동으로 SQLite에 흡수시킨다.

**Tech Stack:** Python 3.13, FastAPI, SQLite (stdlib `sqlite3`), pandas 2.2.3, watchfiles, pykrx, pytest

---

## 파일 맵

| 파일 | 상태 | 책임 |
|---|---|---|
| `app/database/__init__.py` | 신규 | `store` 싱글턴 export |
| `app/database/sqlite_store.py` | 신규 | WAL SQLite 연결, 스키마, upsert, 읽기 |
| `app/watchers/__init__.py` | 신규 | 패키지 마커 |
| `app/watchers/csv_watcher.py` | 신규 | watchfiles CSV 감시 코루틴 |
| `app/repository/market_repo.py` | 수정 | SQLiteStore 통합, 청크 fetch, ingest_csv |
| `app/repository/__init__.py` | 수정 | `store` 추가 export |
| `app/main.py` | 수정 | 4단계 lifespan (초기화→복구→갱신→감시) |
| `requirements.txt` | 수정 | watchfiles, pytest 추가 |
| `tests/conftest.py` | 신규 | 공통 픽스처 |
| `tests/database/test_sqlite_store.py` | 신규 | SQLiteStore 단위 테스트 |
| `tests/repository/test_market_repo.py` | 신규 | MarketRepository 단위 테스트 |
| `tests/watchers/test_csv_watcher.py` | 신규 | CsvWatcher 단위 테스트 |

---

## Task 1: 테스트 인프라 + pytest 설치

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py`
- Create: `tests/database/__init__.py`
- Create: `tests/repository/__init__.py`
- Create: `tests/watchers/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: requirements.txt에 dev 의존성 추가**

```text
fastapi>=0.111.0
uvicorn[standard]>=0.30.0
openpyxl==3.1.5
pandas==2.2.3
pykrx==1.0.48
watchfiles>=0.21.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 2: pytest 설치**

```bash
venv/bin/pip install pytest pytest-asyncio watchfiles
```

Expected output: `Successfully installed pytest-X.X pytest-asyncio-X.X watchfiles-X.X`

- [ ] **Step 3: 디렉토리 및 `__init__.py` 생성**

```bash
mkdir -p tests/database tests/repository tests/watchers
touch tests/__init__.py tests/database/__init__.py tests/repository/__init__.py tests/watchers/__init__.py
```

- [ ] **Step 4: `tests/conftest.py` 작성**

```python
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
```

- [ ] **Step 5: pytest 실행 확인 (수집 0건이어야 정상)**

```bash
venv/bin/pytest tests/ -v
```

Expected: `no tests ran` or `0 passed`

- [ ] **Step 6: 커밋**

```bash
git add requirements.txt tests/
git commit -m "chore: add pytest infra and test directory structure"
```

---

## Task 2: `app/database/sqlite_store.py` — `_df_to_rows` 유틸리티

**Files:**
- Create: `app/database/__init__.py`
- Create: `app/database/sqlite_store.py` (일부)
- Test: `tests/database/test_sqlite_store.py` (일부)

- [ ] **Step 1: 실패 테스트 작성**

`tests/database/test_sqlite_store.py`:

```python
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
    # 두 번째 행: 시가=NaN → None
    시가_idx = 2 + RAW_COLUMNS.index("시가")
    assert rows[1][시가_idx] is None


def test_df_to_rows_inf_becomes_none(sample_df):
    rows = _df_to_rows("KOSPI", sample_df)
    저가_idx = 2 + RAW_COLUMNS.index("저가")
    거래대금_idx = 2 + RAW_COLUMNS.index("거래대금")
    assert rows[1][저가_idx] is None    # inf → None
    assert rows[1][거래대금_idx] is None  # -inf → None


def test_df_to_rows_normal_value_preserved(sample_df):
    rows = _df_to_rows("KOSPI", sample_df)
    종가_idx = 2 + RAW_COLUMNS.index("종가")
    assert rows[0][종가_idx] == 103.0
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
venv/bin/pytest tests/database/test_sqlite_store.py -v
```

Expected: `ImportError: cannot import name '_df_to_rows'`

- [ ] **Step 3: `app/database/__init__.py` 생성**

```python
from app.database.sqlite_store import SQLiteStore, store

__all__ = ["SQLiteStore", "store"]
```

- [ ] **Step 4: `app/database/sqlite_store.py` 작성 (`_df_to_rows`까지)**

```python
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
```

- [ ] **Step 5: 테스트 실행 — PASS 확인**

```bash
venv/bin/pytest tests/database/test_sqlite_store.py -v
```

Expected: `5 passed`

- [ ] **Step 6: 커밋**

```bash
git add app/database/ tests/database/test_sqlite_store.py
git commit -m "feat: add SQLiteStore skeleton and vectorized _df_to_rows"
```

---

## Task 3: `SQLiteStore.initialize()` — WAL + 스키마

**Files:**
- Modify: `app/database/sqlite_store.py`
- Test: `tests/database/test_sqlite_store.py`

- [ ] **Step 1: 실패 테스트 추가** (`tests/database/test_sqlite_store.py` 하단에 추가)

```python
import sqlite3 as _sqlite3


def test_initialize_creates_market_data_table(tmp_path):
    s = SQLiteStore(db_path=tmp_path / "t.db")
    s.initialize()
    conn = _sqlite3.connect(str(tmp_path / "t.db"))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "market_data" in tables
    assert "fetch_checkpoints" in tables
    conn.close()
    s.close()


def test_initialize_sets_wal_mode(tmp_path):
    s = SQLiteStore(db_path=tmp_path / "t.db")
    s.initialize()
    conn = _sqlite3.connect(str(tmp_path / "t.db"))
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    conn.close()
    s.close()


def test_initialize_idempotent(tmp_path):
    s = SQLiteStore(db_path=tmp_path / "t.db")
    s.initialize()
    s.initialize()  # 두 번 호출해도 오류 없어야 함
    s.close()
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
venv/bin/pytest tests/database/test_sqlite_store.py::test_initialize_creates_market_data_table -v
```

Expected: `FAIL` (initialize가 pass만 함)

- [ ] **Step 3: `initialize()` 구현**

`app/database/sqlite_store.py`의 `initialize` 메서드를 교체:

```python
def initialize(self) -> None:
    self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
    self._conn.execute("PRAGMA journal_mode=WAL")
    self._conn.execute("PRAGMA synchronous=NORMAL")
    self._conn.execute(_CREATE_MARKET_DATA)
    self._conn.execute(_CREATE_CHECKPOINTS)
    self._conn.execute(_CREATE_INDEX)
    self._conn.commit()
```

- [ ] **Step 4: 테스트 실행 — PASS 확인**

```bash
venv/bin/pytest tests/database/test_sqlite_store.py -v
```

Expected: `8 passed`

- [ ] **Step 5: 커밋**

```bash
git add app/database/sqlite_store.py tests/database/test_sqlite_store.py
git commit -m "feat: implement SQLiteStore.initialize with WAL and schema"
```

---

## Task 4: `SQLiteStore.upsert_chunk()` — 원자적 트랜잭션

**Files:**
- Modify: `app/database/sqlite_store.py`
- Test: `tests/database/test_sqlite_store.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
def test_upsert_chunk_inserts_rows(store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    conn = _sqlite3.connect(str(store._db_path))
    count = conn.execute("SELECT COUNT(*) FROM market_data WHERE market='KOSPI'").fetchone()[0]
    conn.close()
    assert count == 2


def test_upsert_chunk_no_duplicate_on_repeat(store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")  # 동일 데이터 재삽입
    conn = _sqlite3.connect(str(store._db_path))
    count = conn.execute("SELECT COUNT(*) FROM market_data WHERE market='KOSPI'").fetchone()[0]
    conn.close()
    assert count == 2  # 중복 없이 2건


def test_upsert_chunk_updates_checkpoint(store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    conn = _sqlite3.connect(str(store._db_path))
    date = conn.execute(
        "SELECT last_success_date FROM fetch_checkpoints WHERE market='KOSPI'"
    ).fetchone()[0]
    conn.close()
    assert date == "2025-01-03"


def test_upsert_chunk_none_checkpoint_skips_checkpoint_update(store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, checkpoint_date=None)
    conn = _sqlite3.connect(str(store._db_path))
    row = conn.execute(
        "SELECT last_success_date FROM fetch_checkpoints WHERE market='KOSPI'"
    ).fetchone()
    conn.close()
    assert row is None  # 체크포인트 행 없어야 함


def test_upsert_chunk_empty_df_with_checkpoint(store):
    empty = pd.DataFrame(columns=RAW_COLUMNS)
    empty.index = pd.to_datetime([])
    store.upsert_chunk("KOSPI", empty, "2025-01-03")
    conn = _sqlite3.connect(str(store._db_path))
    date = conn.execute(
        "SELECT last_success_date FROM fetch_checkpoints WHERE market='KOSPI'"
    ).fetchone()[0]
    conn.close()
    assert date == "2025-01-03"  # 빈 df여도 체크포인트 갱신
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
venv/bin/pytest tests/database/test_sqlite_store.py -k "upsert" -v
```

Expected: `5 failed` (NotImplementedError)

- [ ] **Step 3: `upsert_chunk()` 구현**

```python
def upsert_chunk(self, market: str, df: pd.DataFrame, checkpoint_date: str | None = None) -> None:
    with self._write_lock:
        with self._conn:
            if not df.empty:
                rows = _df_to_rows(market, df)
                self._conn.executemany(_UPSERT_SQL, rows)
            if checkpoint_date is not None:
                self._conn.execute(
                    _UPSERT_CHECKPOINT,
                    (market, checkpoint_date, datetime.now().isoformat()),
                )
```

- [ ] **Step 4: 테스트 실행 — PASS 확인**

```bash
venv/bin/pytest tests/database/test_sqlite_store.py -v
```

Expected: `13 passed`

- [ ] **Step 5: 커밋**

```bash
git add app/database/sqlite_store.py tests/database/test_sqlite_store.py
git commit -m "feat: implement SQLiteStore.upsert_chunk with atomic transaction"
```

---

## Task 5: `SQLiteStore` 읽기 메서드

**Files:**
- Modify: `app/database/sqlite_store.py`
- Test: `tests/database/test_sqlite_store.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
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
        {"시가": [float("nan")], "고가": [100.0], "저가": [90.0],
         "종가": [95.0], "거래량": [500.0], "거래대금": [47500.0], "상장시가총액": [1e11]},
        index=pd.to_datetime(["2025-01-02"]),
    )
    store.upsert_chunk("KOSPI", df_with_null)
    df = store.load_market("KOSPI")
    import math
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


def test_markets_in_db(store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    store.upsert_chunk("KOSDAQ", sample_df, "2025-01-03")
    assert set(store.markets_in_db()) == {"KOSPI", "KOSDAQ"}
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
venv/bin/pytest tests/database/test_sqlite_store.py -k "load or checkpoint or dates or markets_in" -v
```

Expected: `7 failed` (NotImplementedError)

- [ ] **Step 3: 읽기 메서드 구현**

```python
def load_market(self, market: str) -> pd.DataFrame:
    cursor = self._conn.execute(_SELECT_MARKET, (market,))
    rows = cursor.fetchall()
    if not rows:
        return pd.DataFrame(columns=RAW_COLUMNS, index=pd.DatetimeIndex([], name="날짜"))
    df = pd.DataFrame(rows, columns=["날짜"] + RAW_COLUMNS)
    df["날짜"] = pd.to_datetime(df["날짜"])
    return df.set_index("날짜")

def get_checkpoint(self, market: str) -> str | None:
    cursor = self._conn.execute(_SELECT_CHECKPOINT, (market,))
    row = cursor.fetchone()
    return row[0] if row else None

def get_all_dates(self, market: str) -> set[str]:
    cursor = self._conn.execute(_SELECT_ALL_DATES, (market,))
    return {row[0] for row in cursor.fetchall()}

def markets_in_db(self) -> list[str]:
    cursor = self._conn.execute(_SELECT_MARKETS)
    return [row[0] for row in cursor.fetchall()]
```

- [ ] **Step 4: 전체 테스트 실행 — PASS 확인**

```bash
venv/bin/pytest tests/database/test_sqlite_store.py -v
```

Expected: `20 passed`

- [ ] **Step 5: 커밋**

```bash
git add app/database/sqlite_store.py tests/database/test_sqlite_store.py
git commit -m "feat: implement SQLiteStore read methods (load_market, get_checkpoint, get_all_dates)"
```

---

## Task 6: `MarketRepository` — SQLiteStore 통합 + `load_from_db`

**Files:**
- Modify: `app/repository/market_repo.py`
- Modify: `app/repository/__init__.py`
- Create: `tests/repository/test_market_repo.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/repository/test_market_repo.py`:

```python
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
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
venv/bin/pytest tests/repository/test_market_repo.py -v
```

Expected: `TypeError: MarketRepository.__init__() got an unexpected keyword argument 'store'`

- [ ] **Step 3: `market_repo.py` 상단 수정 — `store` 파라미터 + 상수 추가**

`app/repository/market_repo.py` 상단의 import 블록과 클래스 정의 변경:

```python
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from pykrx import stock

from app.database.sqlite_store import SQLiteStore, RAW_COLUMNS, store as _default_store

logger = logging.getLogger(__name__)

CSV_PATH = Path(__file__).parent.parent.parent / "data" / "test.csv"
CSV_MARKET = "KOSPI"

MARKET_CODES = {
    "KOSPI":  "1001",
    "KOSDAQ": "2001",
}

_BASE_COLS  = ["시가", "고가", "저가", "종가", "거래대금", "상장시가총액"]
_DISPLAY_COLS = ["종가", "등락폭", "등락률", "시가", "고가", "저가", "거래대금", "상장시가총액"]
```

- [ ] **Step 4: `MarketRepository.__init__` 수정**

```python
class MarketRepository:
    def __init__(self, store: SQLiteStore = _default_store) -> None:
        self._store = store
        self._data: dict[str, pd.DataFrame] = {}
        self._lock = threading.Lock()
```

- [ ] **Step 5: `load_from_db()` 추가**

`MarketRepository` 클래스에 추가:

```python
def load_from_db(self, market: str) -> None:
    df = self._store.load_market(market)
    if df.empty:
        return
    fresh = _add_derived_columns(df)
    with self._lock:
        self._data[market] = fresh
    logger.info("[%s] SQLite 복구 완료: %d행", market, len(fresh))
```

- [ ] **Step 6: `app/repository/__init__.py` 수정**

```python
from .market_repo import MARKET_CODES, MarketRepository, repo
from app.database.sqlite_store import store

__all__ = ["MARKET_CODES", "MarketRepository", "repo", "store"]
```

- [ ] **Step 7: 테스트 실행 — PASS 확인**

```bash
venv/bin/pytest tests/repository/test_market_repo.py -v
```

Expected: `3 passed`

- [ ] **Step 8: 커밋**

```bash
git add app/repository/ tests/repository/
git commit -m "feat: integrate SQLiteStore into MarketRepository, add load_from_db"
```

---

## Task 7: `MarketRepository.load()` — 청크 fetch + prefilter + 체크포인트

**Files:**
- Modify: `app/repository/market_repo.py`
- Test: `tests/repository/test_market_repo.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
from unittest.mock import MagicMock, patch


def test_load_fetches_from_checkpoint(repo, store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    with patch("app.repository.market_repo._fetch_from_pykrx", return_value=pd.DataFrame()) as mock_fetch:
        repo.load("KOSPI")
    # 체크포인트 2025-01-03 → 다음 시작은 2025-01-04
    call_args = mock_fetch.call_args
    assert call_args[0][1] == "2025-01-04"  # start_date


def test_load_prefilter_skips_existing_dates(repo, store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    new_row = pd.DataFrame(
        {"시가": [102.0], "고가": [107.0], "저가": [101.0], "종가": [105.0],
         "거래량": [1200.0], "거래대금": [126000.0], "상장시가총액": [1.2e12]},
        index=pd.to_datetime(["2025-01-06"]),
    )
    with patch("app.repository.market_repo._fetch_from_pykrx", return_value=new_row):
        repo.load("KOSPI")
    df = store.load_market("KOSPI")
    assert len(df) == 3  # 기존 2 + 신규 1


def test_load_break_on_fetch_error_preserves_checkpoint(repo, store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    with patch("app.repository.market_repo._fetch_from_pykrx", side_effect=ConnectionError("KRX down")):
        repo.load("KOSPI")  # 예외 발생해도 load() 자체는 예외 안 던짐
    # 체크포인트 그대로 유지
    assert store.get_checkpoint("KOSPI") == "2025-01-03"


def test_load_updates_memory(repo, store, sample_df):
    store.upsert_chunk("KOSPI", sample_df, "2025-01-03")
    with patch("app.repository.market_repo._fetch_from_pykrx", return_value=pd.DataFrame()):
        repo.load("KOSPI")
    assert repo.is_loaded("KOSPI")
    assert len(repo.get("KOSPI")) == 2
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
venv/bin/pytest tests/repository/test_market_repo.py -k "load" -v
```

Expected: 여러 건 FAIL

- [ ] **Step 3: 헬퍼 함수 추가** (`market_repo.py`, 클래스 외부)

```python
def _fetch_from_pykrx(market_code: str, start_date: str, end_date: str) -> pd.DataFrame:
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
```

- [ ] **Step 4: `load()` 메서드 전체 교체**

기존 `load()` 메서드를 아래로 교체:

```python
def load(self, market: str) -> None:
    code            = MARKET_CODES[market]
    existing_dates  = self._store.get_all_dates(market)   # DB 1회 쿼리
    checkpoint      = self._store.get_checkpoint(market)
    current         = _next_start(checkpoint)
    today           = datetime.today()

    if current <= today:
        while current <= today:
            year_end = min(datetime(current.year, 12, 31), today)
            try:
                chunk    = _fetch_from_pykrx(
                    code,
                    current.strftime("%Y-%m-%d"),
                    year_end.strftime("%Y-%m-%d"),
                )
                filtered = _prefilter_with_set(chunk, existing_dates)
                self._store.upsert_chunk(
                    market,
                    filtered,
                    year_end.strftime("%Y-%m-%d"),
                )
                existing_dates.update(filtered.index.strftime("%Y-%m-%d").tolist())
            except Exception as exc:
                logger.error("[%s] fetch 실패 (%s): %s — 체크포인트 보존", market, year_end.date(), exc)
                break
            current = datetime(current.year + 1, 1, 1)

    fresh = _add_derived_columns(self._store.load_market(market))
    with self._lock:
        self._data[market] = fresh
    logger.info("[%s] 적재 완료: %d행", market, len(fresh))
```

- [ ] **Step 5: 테스트 실행 — PASS 확인**

```bash
venv/bin/pytest tests/repository/test_market_repo.py -v
```

Expected: `7 passed`

- [ ] **Step 6: 커밋**

```bash
git add app/repository/market_repo.py tests/repository/test_market_repo.py
git commit -m "feat: rewrite MarketRepository.load with chunked fetch and prefilter"
```

---

## Task 8: `MarketRepository.ingest_csv()`

**Files:**
- Modify: `app/repository/market_repo.py`
- Test: `tests/repository/test_market_repo.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
import tempfile
from pathlib import Path


def test_ingest_csv_loads_data(repo, store, tmp_path):
    csv_path = tmp_path / "test.csv"
    csv_path.write_text(
        "날짜,시가,고가,저가,종가,거래량,거래대금,상장시가총액\n"
        "2025-06-02,2620.45,2638.90,2611.23,2630.17,412380000,8234500000000,2187650000000000\n"
    )
    # CSV_PATH를 tmp 경로로 교체
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
        repo.ingest_csv()  # 두 번 호출
    assert len(repo.get("KOSPI")) == 1  # 중복 없이 1건


def test_ingest_csv_missing_file_does_not_raise(repo):
    with patch("app.repository.market_repo.CSV_PATH", Path("/nonexistent/path.csv")):
        repo.ingest_csv()  # 예외 없이 조용히 종료
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
venv/bin/pytest tests/repository/test_market_repo.py -k "csv" -v
```

Expected: `AttributeError: 'MarketRepository' object has no attribute 'ingest_csv'`

- [ ] **Step 3: `ingest_csv()` 구현 + `_load_from_csv` 유지**

`market_repo.py`에서 기존 `_load_from_csv` 함수는 그대로 유지하고, 클래스에 `ingest_csv` 추가:

```python
def _load_from_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["날짜"], index_col="날짜")
    df.index = pd.to_datetime(df.index)
    return df.reindex(columns=RAW_COLUMNS)
```

```python
# MarketRepository 클래스 내부
def ingest_csv(self) -> None:
    if not CSV_PATH.exists():
        logger.warning("CSV 파일 없음: %s", CSV_PATH)
        return
    df = _load_from_csv(CSV_PATH)
    self._store.upsert_chunk(CSV_MARKET, df, checkpoint_date=None)
    fresh = _add_derived_columns(self._store.load_market(CSV_MARKET))
    with self._lock:
        self._data[CSV_MARKET] = fresh
    logger.info("[%s] CSV ingest 완료: %d행", CSV_MARKET, len(fresh))
```

- [ ] **Step 4: 테스트 실행 — PASS 확인**

```bash
venv/bin/pytest tests/repository/test_market_repo.py -v
```

Expected: `10 passed`

- [ ] **Step 5: 커밋**

```bash
git add app/repository/market_repo.py tests/repository/test_market_repo.py
git commit -m "feat: add MarketRepository.ingest_csv for CSV auto-ingestion"
```

---

## Task 9: `app/watchers/csv_watcher.py`

**Files:**
- Create: `app/watchers/__init__.py`
- Create: `app/watchers/csv_watcher.py`
- Create: `tests/watchers/test_csv_watcher.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/watchers/test_csv_watcher.py`:

```python
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.watchers.csv_watcher import watch_csv


@pytest.fixture
def on_change():
    return MagicMock()


@pytest.mark.asyncio
async def test_watch_csv_calls_on_change_on_event(tmp_path, on_change):
    csv_path = tmp_path / "test.csv"

    async def fake_awatch(path):
        yield {(1, str(path))}  # 변경 이벤트 1회 발생

    with patch("app.watchers.csv_watcher.awatch", fake_awatch):
        await watch_csv(csv_path, on_change)

    on_change.assert_called_once()


@pytest.mark.asyncio
async def test_watch_csv_continues_after_error(tmp_path):
    call_count = 0

    async def fake_awatch(path):
        yield {(1, str(path))}
        yield {(1, str(path))}

    def failing_callback():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("ingest 실패")

    with patch("app.watchers.csv_watcher.awatch", fake_awatch):
        await watch_csv(tmp_path / "t.csv", failing_callback)

    assert call_count == 2  # 첫 번째 실패 후 두 번째도 호출
```

- [ ] **Step 2: `pytest.ini` 또는 `pyproject.toml`에 asyncio 설정 추가**

프로젝트 루트에 `pytest.ini` 생성:

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 3: 테스트 실행 — FAIL 확인**

```bash
venv/bin/pytest tests/watchers/test_csv_watcher.py -v
```

Expected: `ImportError: cannot import name 'watch_csv'`

- [ ] **Step 4: `app/watchers/__init__.py` 생성**

```python
```
(빈 파일)

- [ ] **Step 5: `app/watchers/csv_watcher.py` 구현**

```python
import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from watchfiles import awatch

logger = logging.getLogger(__name__)


async def watch_csv(csv_path: Path, on_change: Callable[[], None]) -> None:
    async for _ in awatch(csv_path):
        logger.info("CSV 변경 감지: %s", csv_path)
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, on_change)
            logger.info("CSV ingest 완료")
        except Exception as exc:
            logger.error("CSV ingest 실패: %s", exc)
```

- [ ] **Step 6: 테스트 실행 — PASS 확인**

```bash
venv/bin/pytest tests/watchers/test_csv_watcher.py -v
```

Expected: `2 passed`

- [ ] **Step 7: 커밋**

```bash
git add app/watchers/ tests/watchers/ pytest.ini
git commit -m "feat: add CsvWatcher with watchfiles and error-resilient loop"
```

---

## Task 10: `app/main.py` lifespan 교체

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: `app/main.py` 전체 교체**

```python
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.markets import router as markets_router
from app.repository import MARKET_CODES, repo, store
from app.repository.market_repo import CSV_PATH
from app.watchers.csv_watcher import watch_csv

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. SQLite 초기화 (WAL + 테이블 생성)
    store.initialize()

    # 2. SQLite → 인메모리 즉시 복구 (pyKrx 없이 서비스 가능)
    for market in MARKET_CODES:
        try:
            repo.load_from_db(market)
        except Exception as exc:
            logger.warning("SQLite 복구 실패 [%s]: %s", market, exc)

    # 3. pyKrx 증분 갱신 (백그라운드 스레드, 서버 시작 블로킹 없음)
    loop = asyncio.get_event_loop()
    for market in MARKET_CODES:
        loop.run_in_executor(None, repo.load, market)

    # 4. CSV 감시 시작
    csv_task = asyncio.create_task(watch_csv(CSV_PATH, repo.ingest_csv))

    yield

    csv_task.cancel()
    store.close()


app = FastAPI(
    title="KRX Market API",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(markets_router)
```

- [ ] **Step 2: 서버 기동 확인**

```bash
venv/bin/uvicorn app.main:app --reload
```

Expected: 서버 시작 시 로그에서
- `SQLite 복구 완료` 또는 `SQLite 로딩 실패` (최초엔 DB 비어 있으므로 정상)
- pyKrx fetch 로그

- [ ] **Step 3: 전체 테스트 통과 확인**

```bash
venv/bin/pytest tests/ -v
```

Expected: `전체 passed` (실패 없음)

- [ ] **Step 4: 커밋**

```bash
git add app/main.py
git commit -m "feat: update lifespan with 4-step SQLite startup and CSV watcher"
```

---

## Task 11: 불필요 코드 제거 + 최종 정리

**Files:**
- Modify: `app/repository/market_repo.py`

기존 `market_repo.py`에서 SQLite 도입으로 더 이상 필요하지 않은 코드:
- `ConflictResolution` enum — 제거 (DB가 처리)
- `_merge_sources()` 함수 — 제거
- `_CHECK_COLS` 상수 — 제거
- 기존 `load()` 메서드의 `csv_path`, `conflict` 파라미터 — 제거됨 (이미 Task 7에서 교체)

- [ ] **Step 1: `ConflictResolution`, `_merge_sources`, `_CHECK_COLS` 제거 확인**

현재 `market_repo.py`에서 해당 심볼이 여전히 남아 있으면 삭제:

```bash
grep -n "ConflictResolution\|_merge_sources\|_CHECK_COLS" app/repository/market_repo.py
```

잔존 시 해당 블록 삭제.

- [ ] **Step 2: 전체 테스트 최종 확인**

```bash
venv/bin/pytest tests/ -v --tb=short
```

Expected: 모든 테스트 `passed`

- [ ] **Step 3: `repo = MarketRepository()` 모듈 하단 확인**

`market_repo.py` 최하단에 아래가 있어야 함:

```python
repo = MarketRepository()
```

없으면 추가.

- [ ] **Step 4: 서버 재기동 + `/markets/KOSPI/refresh` 호출**

```bash
venv/bin/uvicorn app.main:app --reload
# 별도 터미널에서:
curl -X POST http://localhost:8000/markets/KOSPI/refresh
```

Expected: `{"status":"ok","market":"KOSPI","rows":<N>}`

- [ ] **Step 5: 최종 커밋**

```bash
git add app/repository/market_repo.py
git commit -m "refactor: remove ConflictResolution and _merge_sources (replaced by SQLite upsert)"
```

---

## Self-Review 체크리스트

- [x] **Spec 커버리지**: WAL(Task 3), ON CONFLICT DO UPDATE(Task 4), 원자적 트랜잭션(Task 4), 체크포인트(Task 7), get_all_dates 1회 쿼리(Task 7), _df_to_rows 벡터화(Task 2), CSV 자동 감시(Task 9), 서버 재시작 복구(Task 6+10)
- [x] **플레이스홀더 없음**: 모든 태스크에 실제 코드 포함
- [x] **타입 일관성**: `_df_to_rows`, `upsert_chunk`, `load_market` 시그니처가 전 태스크 걸쳐 일치
- [x] **NULL 처리**: Task 2 `_df_to_rows` + Task 4 `IS NOT` + Task 5 `load_market` 에서 일관되게 처리
