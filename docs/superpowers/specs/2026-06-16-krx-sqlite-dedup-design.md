# KRX SQLite 영속화 & 중복 제거 설계

**날짜**: 2026-06-16  
**상태**: 승인 대기  
**대상 프로젝트**: KRX_GUI (FastAPI + pyKrx)

---

## 1. 목표

| 요구사항 | 해결 방법 |
|---|---|
| CSV · pyKrx 중복 데이터 제거 | SQLite `PRIMARY KEY(market, date)` + `ON CONFLICT DO UPDATE` |
| 5XX(pyKrx) 실패 시 데이터 손실 방지 | 연도별 청크 트랜잭션 + 체크포인트 테이블 |
| 서버 재시작 후 즉각 복구 | 시작 시 SQLite → 인메모리 로딩 (pyKrx 불필요) |
| CSV 자동 흡수 | `watchfiles`로 파일 변경 감지 → SQLite upsert |

---

## 2. 아키텍처 개요

```
data/test.csv ──(watchfiles 감지)──┐
pyKrx API ──(연도별 청크 fetch)────┤
                                   ↓
              app/database/sqlite_store.py
              ┌────────────────────────────┐
              │  market_data               │  ← PRIMARY KEY(market, date)
              │  fetch_checkpoints         │  ← pyKrx 마지막 성공 날짜
              └────────────┬───────────────┘
                           │ load_market()
              app/repository/market_repo.py
              ┌────────────────────────────┐
              │  _data: dict[str, DataFrame] │  ← 조회용 인메모리 캐시
              │  atomic swap (threading.Lock)│
              └────────────┬───────────────┘
                           │
              FastAPI 엔드포인트 (변경 없음)
```

**핵심 원칙**
- SQLite = 진실의 원천(source of truth)
- 인메모리 DataFrame = 조회 전용 캐시
- 중복 제거는 DB 레벨에서 처리 (애플리케이션 로직 없음)
- WAL 모드: 쓰기 중 읽기 블로킹 없음

---

## 3. 디렉토리 구조

```
app/
├── database/
│   ├── __init__.py
│   └── sqlite_store.py     ← 신규: SQLiteStore 클래스
├── watchers/
│   ├── __init__.py
│   └── csv_watcher.py      ← 신규: watchfiles CSV 감시
├── repository/
│   └── market_repo.py      ← 수정: SQLiteStore 통합, 청크 fetch
├── api/
│   └── markets.py          ← 변경 없음
├── services/
│   └── market.py           ← 변경 없음
└── main.py                 ← 수정: lifespan 확장

data/
└── test.csv                ← 외부 주입 채널 (변경 없음)
krx_market.db               ← 신규: SQLite DB 파일 (프로젝트 루트)
```

---

## 4. SQLite 스키마

```sql
PRAGMA journal_mode=WAL;       -- 동시 읽기/쓰기 허용
PRAGMA synchronous=NORMAL;     -- WAL에서 안전, fsync 오버헤드 감소

CREATE TABLE IF NOT EXISTS market_data (
    market         TEXT NOT NULL,
    date           TEXT NOT NULL,   -- YYYY-MM-DD
    시가           REAL,            -- NULL 허용 (결측 데이터 자연 저장)
    고가           REAL,
    저가           REAL,
    종가           REAL,
    거래량         REAL,
    거래대금       REAL,
    상장시가총액   REAL,
    PRIMARY KEY (market, date)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS fetch_checkpoints (
    market            TEXT PRIMARY KEY,
    last_success_date TEXT NOT NULL,   -- YYYY-MM-DD
    updated_at        TEXT NOT NULL    -- ISO 8601
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_market_data_date
    ON market_data (market, date DESC);
```

### Upsert 구문

`INSERT OR REPLACE` 대신 `ON CONFLICT DO UPDATE` 사용.  
→ 삭제·재삽입 없음, 인덱스 재정렬 비용 제거.

```sql
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
WHERE                                  -- 실제 변경 시에만 디스크 쓰기
    excluded.종가     IS NOT market_data.종가
    OR excluded.시가  IS NOT market_data.시가
    OR excluded.고가  IS NOT market_data.고가
    OR excluded.저가  IS NOT market_data.저가;
```

**`IS NOT` 사용 이유**: SQL의 `!=`는 `NULL != NULL → NULL`이라 NULL 변경을 감지하지 못함.

---

## 5. NULL / 결측값 처리

| 레이어 | 처리 방식 |
|---|---|
| 스키마 | 데이터 컬럼 `NOT NULL` 없음 → NULL 자유롭게 저장 |
| Python 삽입 전 | `df.replace([inf, -inf], NaN).to_numpy(dtype=object, na_value=None)` |
| Upsert WHERE | `IS NOT` → NULL 변경도 감지 |
| SQLite → pandas | `NULL → None → NaN` 자동 복원 |

### 벡터화 행 변환 (`_df_to_rows`)

```python
def _df_to_rows(market: str, df: pd.DataFrame) -> list[tuple]:
    clean = (
        df[RAW_COLUMNS]
        .replace([math.inf, -math.inf], float("nan"))  # inf → NaN (벡터)
        .to_numpy(dtype=object, na_value=None)          # NaN → None (벡터)
        .tolist()
    )
    dates = df.index.strftime("%Y-%m-%d").tolist()
    return [(market, date, *row) for date, row in zip(dates, clean)]
```

`iterrows` 루프·`_clean()` 행별 호출 없음. ~50–100× 성능 향상.

---

## 6. 컴포넌트 상세

### 6-1. `SQLiteStore` (`app/database/sqlite_store.py`)

| 메서드 | 역할 |
|---|---|
| `initialize()` | WAL 설정, 테이블/인덱스 생성 |
| `upsert_chunk(market, df, checkpoint_date)` | 삽입 + 체크포인트를 단일 트랜잭션으로. `checkpoint_date=None`이면 체크포인트 갱신 생략 (CSV ingest용) |
| `load_market(market)` | SQLite → DataFrame (락 불필요, WAL) |
| `get_checkpoint(market)` | `last_success_date` 반환 |
| `get_all_dates(market)` | 해당 시장 전체 날짜 Set 반환 (1회 쿼리) |
| `markets_in_db()` | DB에 데이터 있는 시장 목록 |
| `close()` | 연결 종료 |

**동시성**: `threading.Lock()`으로 쓰기 직렬화, WAL로 읽기는 쓰기와 독립.

### 6-2. `MarketRepository` (`app/repository/market_repo.py`) 수정

```
load(market):
  1. get_all_dates(market)  ← DB 쿼리 1회만
  2. get_checkpoint(market) ← 시작 날짜 결정
  3. while 연도별 청크:
       fetch pyKrx
       _prefilter_with_set(chunk, existing_dates)  ← 인메모리 Set 비교
       upsert_chunk()  ← 트랜잭션: 삽입 + 체크포인트
       existing_dates.update()
       실패 시 break (체크포인트 보존)
  4. load_market() → atomic swap → _data[market]

load_from_db(market):
  SQLite → in-memory (pyKrx 없이, 서버 시작용)

ingest_csv():
  CSV 읽기 → upsert_chunk() → _data 갱신
```

**메모리 1차 필터 (`_prefilter_with_set`)**:
```python
def _prefilter_with_set(df: pd.DataFrame, existing_dates: set[str]) -> pd.DataFrame:
    return df[~df.index.strftime("%Y-%m-%d").isin(existing_dates)]
```
루프 안에서 DB 쿼리 없음. `get_all_dates`로 미리 로딩한 Set 대상으로 O(1) 룩업.

### 6-3. `CsvWatcher` (`app/watchers/csv_watcher.py`)

```python
async def watch_csv(csv_path: Path, on_change: Callable) -> None:
    async for _ in awatch(csv_path):
        try:
            await asyncio.get_event_loop().run_in_executor(None, on_change)
        except Exception as exc:
            logger.error("CSV ingest 실패: %s", exc)
            # 실패해도 감시 루프 계속
```

- `run_in_executor`: 동기 `ingest_csv()`를 스레드풀에서 실행 → 이벤트 루프 블로킹 없음
- CSV 연관 시장: `market_repo.py` 최상단 모듈 상수 `CSV_MARKET = "KOSPI"`로 설정. 변경 시 이 값만 수정.

### 6-4. `main.py` lifespan 수정

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    store.initialize()                        # 1. WAL + 테이블 생성

    for market in MARKET_CODES:               # 2. SQLite → 인메모리 즉시 복구
        repo.load_from_db(market)             #    (pyKrx 없이 서비스 즉각 가능)

    loop = asyncio.get_event_loop()
    for market in MARKET_CODES:               # 3. pyKrx 증분 갱신 (백그라운드)
        loop.run_in_executor(None, repo.load, market)

    csv_task = asyncio.create_task(           # 4. CSV 감시 시작
        watch_csv(CSV_PATH, repo.ingest_csv)
    )
    yield
    csv_task.cancel()
    store.close()
```

---

## 7. 원자적 트랜잭션 단위

```
연도별 청크 하나 = 1 트랜잭션

BEGIN
  executemany(UPSERT_SQL, chunk_rows)        ← ~250행
  UPDATE fetch_checkpoints
    SET last_success_date = ?, updated_at = ?
    WHERE market = ?
COMMIT
```

- 청크 삽입 성공 → 체크포인트 갱신 → 다음 청크
- pyKrx 실패 → ROLLBACK → 체크포인트 이전 값 유지 → 다음 `/refresh` 시 실패 지점부터 재개
- 체크포인트 갱신 SQL: `INSERT OR REPLACE INTO fetch_checkpoints ...` (행 없을 때도 안전)
- `checkpoint_date=None`일 때(CSV ingest): 체크포인트 SQL 실행 생략, 데이터 삽입만 수행

---

## 8. 전체 데이터 흐름 요약

```
서버 시작
  store.initialize()
  → load_from_db()         빠른 복구 (SQLite 기반)
  → run_in_executor(load)  pyKrx 증분 갱신 (백그라운드)
  → watch_csv 태스크 시작

CSV 변경 감지
  → run_in_executor(ingest_csv)
  → upsert_chunk()  [트랜잭션]
  → atomic swap

POST /markets/{market}/refresh
  → get_all_dates()         DB 1회 쿼리
  → 연도별 청크 루프
      _prefilter_with_set() 인메모리 필터
      upsert_chunk()         트랜잭션
      existing_dates.update()
  → atomic swap

GET /markets/{market}/...
  → repo.get()              인메모리 (WAL: 쓰기 중에도 읽기 허용)
```

---

## 9. 변경되지 않는 것

- `app/api/markets.py` — 엔드포인트 인터페이스 불변
- `app/services/market.py` — 비즈니스 로직 불변
- `ConflictResolution` enum — CSV 병합 정책 (초기 마이그레이션 시 활용)
- `data/test.csv` 포맷 — 외부 주입 채널 역할 유지

---

## 10. 의존성 추가

```
watchfiles  (이미 설치됨)
sqlite3     (Python 내장, 추가 설치 없음)
```
