# KRX Market API

[![Python 3.13](https://img.shields.io/badge/Python-3.13-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-green)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Korean stock market data aggregator for KOSPI and KOSDAQ using FastAPI, pyKrx, and SQLite.

## 기능

- **다중 데이터 소스**: pyKrx (실시간 금융 API) + SQLite (영속 저장소) + CSV (수동 주입)
- **인메모리 캐시**: 모든 API 응답을 빠른 캐시에서 제공
- **SQLite WAL 모드**: 체크포인트 기반 복구 — 5XX 에러 시 데이터 손실 없음
- **중복 제거**: `ON CONFLICT DO UPDATE` — CSV와 pyKrx 데이터 자동 병합
- **CSV 자동 감시**: watchfiles로 파일 변경 감지 및 즉시 수집 (재시작 불필요)
- **백그라운드 동기화**: 시작 시 pyKrx 증분 갱신
- **스레드 안전**: 원자적 스왑으로 안전한 인메모리 캐시 업데이트
- **Excel 다운로드**: 필터링된 데이터를 여러 형식으로 일괄 내보내기

## 빠른 시작

### 1. 저장소 복제
```bash
git clone https://github.com/yourusername/KRX_GUI.git
cd KRX_GUI
```

### 2. 가상환경 생성
```bash
python -m venv venv
source venv/bin/activate  # macOS/Linux
# 또는
venv\Scripts\activate  # Windows
```

### 3. 의존성 설치
```bash
pip install -r requirements.txt
```

### 4. 서버 실행
```bash
python main.py
```

서버가 `http://0.0.0.0:8000` 에서 시작됩니다.

### 5. API 문서 확인
브라우저에서 http://localhost:8000/docs (Swagger UI) 또는 http://localhost:8000/redoc 를 엽니다.

## API 엔드포인트

모든 엔드포인트는 `/markets` 접두어를 사용합니다.

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/` | 사용 가능한 시장 목록 (KOSPI, KOSDAQ) |
| POST | `/{market}/refresh` | pyKrx에서 수동 데이터 갱신 |
| GET | `/{market}/filter` | 조건 필터링 (종가, 등락률, 거래대금 등) |
| GET | `/{market}/filter/export` | 필터 결과 Excel 다운로드 |
| GET | `/{market}/latest-match` | 조건 만족하는 가장 최근 일자 조회 |
| GET | `/{market}/today` | 오늘 기준값 직전 일자 비교 |
| GET | `/{market}/yearly` | 연도별 일평균 거래대금 + 연말 등락률 |
| GET | `/{market}/yearly/export` | 연도별 정보 Excel 다운로드 |
| GET | `/{market}/consecutive` | 연속 상승/하락 일자 분석 |
| GET | `/{market}/consecutive/export` | 연속 분석 Excel 다운로드 |
| GET | `/{market}/top` | 기준별 상위/하위 N개 조회 |
| GET | `/{market}/top/export` | 상위/하위 N개 Excel 다운로드 |

### 쿼리 파라미터

- **criteria** (필수, 필터/top에만): `종가` | `장중 가격` | `등락률` | `등락폭` | `상장시가총액` | `거래대금`
- **operator** (필수, 필터/top에만): `이상` | `이하`
- **value** (필수, 필터/top에만): 기준값 (float)
- **n** (선택, top에만): 상위/하위 개수 (1-1000, 기본값 10)
- **top_n** (선택, consecutive에만): 상위 개수 (1-100, 기본값 10)

## CSV 데이터 주입

### 준비

1. `data/kospi_data.csv` 파일 생성 (디렉토리는 `.gitignore` 포함)
2. 필수 컬럼: `날짜` (인덱스), `시가`, `고가`, `저가`, `종가`, `거래량`, `거래대금`, `상장시가총액`

### 파일 형식

```csv
날짜,시가,고가,저가,종가,거래량,거래대금,상장시가총액
2020-01-02,2204.75,2211.44,2190.18,2203.43,1076848929,2371897139330000,1337520000000000
2020-01-03,2220.68,2228.50,2212.30,2223.23,1107127281,2468866125690000,1346580000000000
...
```

### 자동 감시

- 파일 변경이 감지되면 자동으로 수집됨 (재시작 불필요)
- 인메모리 캐시가 즉시 갱신됨
- SQLite에 영속됨 (중복 제거됨)

## 아키텍처

- **FastAPI 앱** (`app/main.py`): lifespan 이벤트로 초기화, 백그라운드 동기화, CSV 감시 관리
- **SQLite WAL** (`app/database/sqlite_store.py`): 체크포인트 기반 복구, upsert 로직, 스레드 안전
- **Repository 패턴** (`app/repository/market_repo.py`): pyKrx 페치, CSV 수집, 인메모리 캐시 관리
- **API 라우터** (`app/api/markets.py`): REST 엔드포인트, Excel 다운로드
- **비즈니스 로직** (`app/services/market.py`): 필터링, 상위/하위, 연속 상승/하락 분석
- **CSV 감시** (`app/watchers/csv_watcher.py`): watchfiles 기반 비동기 파일 감시

더 자세한 내용은 [docs/technical-spec.md](docs/technical-spec.md) 를 참고하세요.

## 테스트

### 전체 테스트 실행
```bash
pytest
```

### 특정 테스트 모듈 실행
```bash
pytest tests/database/
pytest tests/repository/
pytest tests/watchers/
```

### 커버리지 확인
```bash
pytest --cov=app --cov-report=html
```

### 비동기 테스트
테스트는 `pytest-asyncio` (asyncio_mode = auto)를 사용하여 자동으로 비동기 모드를 감지합니다.

## 디렉토리 구조

```
KRX_GUI/
├── main.py                      # uvicorn 진입점 (python main.py)
├── requirements.txt             # 의존성 목록
├── pytest.ini                   # pytest 설정
├── README.md                    # 이 파일
├── app/
│   ├── main.py                 # FastAPI 앱 + lifespan (초기화, 백그라운드 작업)
│   ├── api/
│   │   ├── __init__.py
│   │   └── markets.py          # REST 엔드포인트 (필터, top, yearly 등)
│   ├── repository/
│   │   ├── __init__.py
│   │   └── market_repo.py      # pyKrx 페치 + CSV 수집 + 인메모리 캐시
│   ├── database/
│   │   ├── __init__.py
│   │   └── sqlite_store.py     # SQLite WAL 모드 + upsert + 스레드 안전
│   ├── services/
│   │   ├── __init__.py
│   │   └── market.py           # 데이터 분석 (필터, top, 연속상승 등)
│   └── watchers/
│       ├── __init__.py
│       └── csv_watcher.py      # watchfiles 기반 비동기 파일 감시
├── tests/
│   ├── conftest.py             # pytest 픽스처
│   ├── test_lifespan.py        # lifespan 이벤트 테스트
│   ├── database/
│   │   ├── __init__.py
│   │   └── test_sqlite_store.py
│   ├── repository/
│   │   ├── __init__.py
│   │   └── test_market_repo.py
│   └── watchers/
│       ├── __init__.py
│       └── test_csv_watcher.py
├── docs/
│   └── technical-spec.md       # 아키텍처 다이어그램, 스키마, 비동기 패턴
├── data/                       # .gitignore — kospi_data.csv를 여기 놓기
└── venv/                       # 가상환경
```

## 기술 스택

- **Python 3.13**: 최신 Python 버전
- **FastAPI 0.111+**: 고성능 웹 프레임워크
- **pandas 2.2.3**: 데이터 분석 및 조작
- **pykrx 1.0.48**: 한국 주식 시장 API
- **SQLite (WAL mode)**: 표준 라이브러리의 내장 데이터베이스
- **watchfiles 0.21+**: 비동기 파일 감시
- **openpyxl 3.1.5**: Excel 파일 생성
- **pytest 8.0+**: 테스트 프레임워크
- **pytest-asyncio 0.23+**: 비동기 테스트 지원
