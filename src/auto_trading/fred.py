"""세인트루이스 연방준비은행 FRED에서 국채 금리, 기준금리 같은 거시지표를
일별로 받는다.

`yahoo.py`와 반환 형태를 맞춰서(trade_date, open, high, low, close,
volume) 기존 시세 저장·병합·업로드 코드를 그대로 쓸 수 있게 한다. 거시지표는
시가·고가·저가·거래량이 없으므로 종가(close) 하나에 시가·고가·저가를
그대로 맞추고 거래량은 0으로 채운다.

**공식 API(api.stlouisfed.org)를 인증 키와 함께 쓴다.** 처음에는 로그인이나
키가 필요 없는 공개 CSV 주소(fredgraph.csv)로 만들었는데, GitHub Actions에서
실제로 돌려 보니 15초, 60초 둘 다 응답을 기다리다 타임아웃 났다(2026-09-30,
실행 36699155014, 36699330827). 같은 계정의 다른 프로젝트(LXGroup_지표수집)가
이미 공식 API로 FRED를 받고 있어서 그 방식으로 바꿨다. api_key는
`FRED_API_KEY` 환경변수로 받는다(`scripts/fetch_macro.py`가 GitHub Actions
시크릿에서 읽어 넘긴다).

FRED는 결측값을 "."으로 표시한다(휴장일이 아니라 통계를 아직 안 낸 날).
그 줄은 버린다.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import requests

OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"


def fetch_daily_series(series_id: str, start: date, end: date, api_key: str, timeout: float = 30.0) -> pd.DataFrame:
    """series_id는 FRED 시리즈 코드 그대로 쓴다(국채 10년물은 DGS10, 기준금리는
    FEDFUNDS)."""
    response = requests.get(
        OBSERVATIONS_URL,
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": start.isoformat(),
            "observation_end": end.isoformat(),
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return _parse_observations(response.json())


def _parse_observations(payload: dict) -> pd.DataFrame:
    columns = ["trade_date", "open", "high", "low", "close", "volume"]
    observations = payload.get("observations") or []
    if not observations:
        return pd.DataFrame(columns=columns)

    trade_date = pd.to_datetime([o.get("date") for o in observations], errors="coerce").date
    raw_values = [o.get("value") for o in observations]
    close = pd.to_numeric([None if v == "." else v for v in raw_values], errors="coerce")

    df = pd.DataFrame({"trade_date": trade_date, "close": close}).dropna(subset=["trade_date", "close"])
    df["open"] = df["close"]
    df["high"] = df["close"]
    df["low"] = df["close"]
    df["volume"] = 0
    return df[columns].sort_values("trade_date").reset_index(drop=True)
