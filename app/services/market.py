import pandas as pd


def _normalize_criteria(criteria: str, operator: str) -> str:
    if criteria == "장중 가격":
        return "고가" if operator == "이상" else "저가"
    return criteria


_DISPLAY_COLS = ["종가", "등락폭", "등락률", "시가", "고가", "저가", "거래대금", "상장시가총액"]


def filter_data(
    data: pd.DataFrame,
    criteria: str,
    operator: str,
    value: float,
) -> pd.DataFrame:
    col      = _normalize_criteria(criteria, operator)
    filtered = (
        data[data[col] >= value]
        if operator == "이상"
        else data[data[col] <= value]
    )
    out = filtered[_DISPLAY_COLS].copy()
    out.insert(0, "일자", out.index.strftime("%Y-%m-%d"))
    return out.reset_index(drop=True)


def get_latest_match(
    data: pd.DataFrame,
    criteria: str,
    operator: str,
    value: float,
) -> dict:
    col      = _normalize_criteria(criteria, operator)
    filtered = (
        data[data[col] >= value]
        if operator == "이상"
        else data[data[col] <= value]
    )

    if filtered.empty:
        return {"found": False}

    latest_idx   = filtered.index[-1]
    latest_value = filtered.iloc[-1][col]
    rank         = int(data[col].rank(ascending=False, method="min")[latest_idx])

    return {
        "found":       True,
        "criteria":    col,
        "latest_date": latest_idx.strftime("%Y-%m-%d"),
        "value":       latest_value,
        "rank":        rank,
        "total":       len(data),
    }


def get_today_info(data: pd.DataFrame, operator: str) -> list[dict]:
    today_date = data.index[-1]
    previous   = data.iloc[:-1]

    criteria_list = (
        ["종가", "고가", "등락률", "등락폭", "거래대금", "상장시가총액"]
        if operator == "이상"
        else ["종가", "저가", "등락률", "등락폭", "거래대금", "상장시가총액"]
    )

    results: list[dict] = []
    for col in criteria_list:
        today_value = data.iloc[-1][col]
        filtered = (
            previous[previous[col] >= today_value]
            if operator == "이상"
            else previous[previous[col] <= today_value]
        )

        if not filtered.empty:
            latest_idx   = filtered.index[-1]
            results.append({
                "criteria":              col,
                "today_value":           today_value,
                "found":                 True,
                "latest_matching_date":  latest_idx.strftime("%Y-%m-%d"),
                "latest_matching_value": filtered.iloc[-1][col],
                "days_ago":              (today_date - latest_idx).days,
                "rank":                  int(data[col].rank(ascending=False, method="min")[today_date]),
                "total":                 len(data),
            })
        else:
            results.append({"criteria": col, "today_value": today_value, "found": False})

    return results


def _calc_yearly_eoy(data: pd.DataFrame, special_year: int | None = None) -> pd.DataFrame:
    end_of_year   = data.resample("YE").apply(lambda d: d["종가"].iloc[-1] if not d.empty else None)
    start_of_year = data.resample("YE").apply(lambda d: d["종가"].iloc[0]  if not d.empty else None)
    df = pd.DataFrame({"연말종가": end_of_year, "연초종가": start_of_year}).dropna()

    if special_year and special_year in df.index:
        df.loc[special_year, "연말종가등락률"] = (
            (end_of_year.loc[special_year] - start_of_year.loc[special_year])
            / start_of_year.loc[special_year] * 100
        )

    df["연말종가등락률"] = df["연말종가"].pct_change() * 100
    return df


def get_yearly_info(data: pd.DataFrame, market: str) -> pd.DataFrame:

    yearly_avg = data.resample("YE").agg({"거래대금": "mean"})
    yearly_avg["거래대금 등락률"] = yearly_avg["거래대금"].pct_change() * 100
    yearly_avg.at[yearly_avg.index[0], "거래대금 등락률"] = pd.NA
    yearly_avg = yearly_avg.dropna()

    special = 1980 if market == "KOSPI" else (1996 if market == "KOSDAQ" else None)
    eoy = _calc_yearly_eoy(data, special)

    last_dates    = data.resample("YE").apply(lambda x: x.index[-1].strftime("%Y-%m-%d"))
    last_dates_df = pd.DataFrame({"마지막 거래일": last_dates["종가"]})

    yearly_info = yearly_avg.join(eoy, how="inner").join(last_dates_df, how="inner")
    yearly_info["연도"] = yearly_info.index.year
    yearly_info = yearly_info.reset_index(drop=True)

    if market == "KOSPI" and 1980 not in yearly_info["연도"].values:
        end_price = 106.87
        new_row = pd.DataFrame({
            "연도":           [1980],
            "마지막 거래일":  [data[data.index.year == 1980].index[-1].strftime("%Y-%m-%d")],
            "연말종가":       [end_price],
            "연말종가등락률": [(end_price - 100) / 100 * 100],
            "거래대금":       [data[data.index.year == 1980]["거래대금"].mean()],
            "거래대금 등락률": [pd.NA],
        })
        yearly_info = pd.concat([new_row, yearly_info], ignore_index=True)

    elif market == "KOSDAQ" and 1996 not in yearly_info["연도"].values:
        end_price = data[data.index.year == 1996]["종가"].iloc[-1]
        new_row = pd.DataFrame({
            "연도":           [1996],
            "마지막 거래일":  [data[data.index.year == 1996].index[-1].strftime("%Y-%m-%d")],
            "연말종가":       [end_price],
            "연말종가등락률": [(end_price - 1000) / 1000 * 100],
            "거래대금":       [data[data.index.year == 1996]["거래대금"].mean()],
            "거래대금 등락률": [pd.NA],
        })
        yearly_info = pd.concat([new_row, yearly_info], ignore_index=True)

    return yearly_info[["연도", "마지막 거래일", "연말종가", "연말종가등락률", "거래대금", "거래대금 등락률"]]


def calculate_consecutive_days(data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = data.copy()
    df["상승"] = df["종가"].diff().map(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    df["그룹"] = (df["상승"] != df["상승"].shift(1)).cumsum()

    result = df.groupby("그룹").agg(
        방향=("상승", "first"),
        연속일수=("종가", "size"),
        시작일종가=("종가", "first"),
        마지막일종가=("종가", "last"),
    ).reset_index(drop=True)

    result["시작일자"] = (
        df.groupby("그룹").apply(lambda x: x.index.min().strftime("%Y-%m-%d"))
        .reset_index(drop=True)
    )
    result["마감일자"] = (
        df.groupby("그룹").apply(lambda x: x.index.max().strftime("%Y-%m-%d"))
        .reset_index(drop=True)
    )

    df["시작전일종가"] = df["종가"].shift(1).fillna(df["종가"])
    result["시작전일종가"] = (
        df.groupby("그룹").apply(lambda x: x["시작전일종가"].iloc[0])
        .reset_index(drop=True)
    )
    result["등락률"] = (
        ((result["마지막일종가"] - result["시작전일종가"]) / result["시작전일종가"]) * 100
    ).replace([float("inf"), -float("inf")], 0).fillna(0).round(2)

    상승 = result[result["방향"] > 0].sort_values("연속일수", ascending=False)
    하락 = result[result["방향"] < 0].sort_values("연속일수", ascending=False)
    return 상승, 하락


def get_top_n(
    data: pd.DataFrame,
    criteria: str,
    operator: str,
    n: int = 10,
) -> pd.DataFrame:
    col = _normalize_criteria(criteria, operator)
    top = (
        data.nlargest(n, col)
        if operator == "이상"
        else data.nsmallest(n, col)
    )
    out = top[_DISPLAY_COLS].copy()
    out.insert(0, "일자", out.index.strftime("%Y-%m-%d"))
    return out.reset_index(drop=True)
