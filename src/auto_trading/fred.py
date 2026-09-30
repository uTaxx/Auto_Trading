"""세인트루이스 연방준비은행 FRED에서 국채 금리, 기준금리 같은 거시지표를
일별로 받는다.

muwon406과 마찬가지로 로그인이나 키가 필요 없는 공개 CSV 주소
(`https://fred.stlouisfed.org/graph/fredgraph.csv?id=<시리즈>`)를 쓴다.
`yahoo.py`와 반환 형태를 맞춰서(trade_date, open, high, low, close,
volume) 기존 시세 저장·병합·업로드 코드를 그대로 쓸 수 있게 한다. 거시지표는
시가·고가·저가·거래량이 없으므로 종가(close) 하나에 시가·고가·저가를
그대로 맞추고 거래량은 0으로 채운다.

FRED는 결측값을 "."으로 표시한다(휴장일이 아니라 통계를 아직 안 낸 날).
그 줄은 버린다.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import requests

GRAPH_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def fetch_daily_series(series_id: str, start: date, end: date, timeout: float = 15.0) -> pd.DataFrame:
    """series_id는 FRED 시리즈 코드 그대로 쓴다(국채 10년물은 DGS10, 기준금리는
    FEDFUNDS)."""
    response = requests.get(
        GRAPH_CSV_URL,
        params={"id": series_id, "cosd": start.isoformat(), "coed": end.isoformat()},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=timeout,
    )
    response.raise_for_status()
    return _parse_graph_csv(response.text)


def _parse_graph_csv(text: str) -> pd.DataFrame:
    columns = ["trade_date", "open", "high", "low", "close", "volume"]
    raw = pd.read_csv(pd.io.common.StringIO(text))
    if raw.shape[1] < 2:
        return pd.DataFrame(columns=columns)

    date_col, value_col = raw.columns[0], raw.columns[1]
    trade_date = pd.to_datetime(raw[date_col], errors="coerce").dt.date
    close = pd.to_numeric(raw[value_col], errors="coerce")

    df = pd.DataFrame({"trade_date": trade_date, "close": close}).dropna(subset=["trade_date", "close"])
    df["open"] = df["close"]
    df["high"] = df["close"]
    df["low"] = df["close"]
    df["volume"] = 0
    return df[columns].sort_values("trade_date").reset_index(drop=True)
