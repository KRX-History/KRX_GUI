import io
from typing import Annotated, Literal

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.repository import MARKET_CODES, repo
from app.services import (
    calculate_consecutive_days,
    filter_data,
    get_latest_match,
    get_today_info,
    get_top_n,
    get_yearly_info,
)

router = APIRouter(prefix="/markets", tags=["markets"])

Market   = Literal["KOSPI", "KOSDAQ"]
Criteria = Literal["종가", "장중 가격", "등락률", "등락폭", "상장시가총액", "거래대금"]
Operator = Literal["이상", "이하"]


def _get_data(market: Market) -> pd.DataFrame:
    if not repo.is_loaded(market):
        raise HTTPException(
            status_code=503,
            detail=f"'{market}' 데이터가 로드되지 않았습니다. POST /markets/{market}/refresh 를 먼저 호출하세요.",
        )
    return repo.get(market)


def _excel_stream(df: pd.DataFrame, filename: str, sheet_name: str = "Sheet1") -> StreamingResponse:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("", summary="사용 가능한 시장 목록")
def list_markets():
    return {"markets": list(MARKET_CODES.keys())}


@router.post("/{market}/refresh", summary="시장 데이터 최신화")
def refresh_market(market: Market):
    try:
        repo.load(market)
        return {"status": "ok", "market": market, "rows": len(repo.get(market))}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{market}/filter", summary="조건 만족 데이터 조회")
def filter_market_data(
    market: Market,
    criteria: Annotated[Criteria, Query(description="기준 항목")],
    operator: Annotated[Operator, Query(description="이상 / 이하")],
    value: Annotated[float, Query(description="기준 값")],
):
    result = filter_data(_get_data(market), criteria, operator, value)
    return {"market": market, "count": len(result), "data": result.to_dict(orient="records")}


@router.get("/{market}/filter/export", summary="조건 만족 데이터 엑셀 다운로드")
def export_filtered_data(
    market: Market,
    criteria: Annotated[Criteria, Query()],
    operator: Annotated[Operator, Query()],
    value: Annotated[float, Query()],
):
    result = filter_data(_get_data(market), criteria, operator, value)
    if result.empty:
        raise HTTPException(status_code=404, detail="조건에 맞는 데이터가 없습니다.")
    return _excel_stream(result, f"{market}_filtered.xlsx")


@router.get("/{market}/latest-match", summary="조건 만족 가장 최근 일자 조회")
def latest_match(
    market: Market,
    criteria: Annotated[Criteria, Query()],
    operator: Annotated[Operator, Query()],
    value: Annotated[float, Query()],
):
    result = get_latest_match(_get_data(market), criteria, operator, value)
    if not result["found"]:
        raise HTTPException(status_code=404, detail="조건에 맞는 데이터가 없습니다.")
    return result


@router.get("/{market}/today", summary="오늘 기준 값 직전 일자 조회")
def today_info(
    market: Market,
    operator: Annotated[Operator, Query()] = "이상",
):
    data       = _get_data(market)
    today_date = data.index[-1].strftime("%Y-%m-%d")
    return {
        "market":   market,
        "date":     today_date,
        "operator": operator,
        "results":  get_today_info(data, operator),
    }


@router.get("/{market}/yearly", summary="연도별 일평균 거래대금 및 연말 종가 등락률")
def yearly_info(market: Market):
    df = get_yearly_info(_get_data(market), market)
    return {"market": market, "data": df.to_dict(orient="records")}


@router.get("/{market}/yearly/export", summary="연도별 정보 엑셀 다운로드")
def export_yearly_info(market: Market):
    df = get_yearly_info(_get_data(market), market).copy()
    df["거래대금"]        = df["거래대금"].map(lambda x: f"{x:,.0f}")
    df["거래대금 등락률"] = df["거래대금 등락률"].map(lambda x: f"{x:.2f}%" if pd.notna(x) else "")
    df["연말종가등락률"]  = df["연말종가등락률"].map(lambda x: f"{x:.2f}%")
    df["연말종가"]        = df["연말종가"].map(lambda x: f"{x:,.2f}")
    return _excel_stream(df, f"{market}_yearly.xlsx", sheet_name="연도별정보")


@router.get("/{market}/consecutive", summary="연속 상승/하락 일자 조회")
def consecutive_days(
    market: Market,
    top_n: Annotated[int, Query(ge=1, le=100)] = 10,
):
    상승, 하락 = calculate_consecutive_days(_get_data(market))
    return {
        "market":      market,
        "up_streaks":  상승.head(top_n).to_dict(orient="records"),
        "down_streaks": 하락.head(top_n).to_dict(orient="records"),
    }


@router.get("/{market}/consecutive/export", summary="연속 상승/하락 엑셀 다운로드")
def export_consecutive_days(market: Market):
    상승, 하락 = calculate_consecutive_days(_get_data(market))
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        상승.to_excel(writer, sheet_name="상승_일자", index=False)
        하락.to_excel(writer, sheet_name="하락_일자", index=False)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{market}_consecutive.xlsx"'},
    )


@router.get("/{market}/top", summary="상위/하위 N위 조회")
def top_n(
    market: Market,
    criteria: Annotated[Criteria, Query()],
    operator: Annotated[Operator, Query()],
    n: Annotated[int, Query(ge=1, le=1000)] = 10,
):
    result = get_top_n(_get_data(market), criteria, operator, n)
    return {"market": market, "n": n, "count": len(result), "data": result.to_dict(orient="records")}


@router.get("/{market}/top/export", summary="상위/하위 N위 엑셀 다운로드")
def export_top_n(
    market: Market,
    criteria: Annotated[Criteria, Query()],
    operator: Annotated[Operator, Query()],
    n: Annotated[int, Query(ge=1, le=1000)] = 100,
):
    result = get_top_n(_get_data(market), criteria, operator, n)
    if result.empty:
        raise HTTPException(status_code=404, detail="데이터가 없습니다.")
    return _excel_stream(result, f"{market}_top{n}.xlsx")
