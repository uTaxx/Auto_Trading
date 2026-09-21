"""야후 파이낸스 공개 차트 API로 일봉 시세를 받는다.

muwon406(src/muwon/data/yahoo_client.py)과 같은 방식이다. 로그인이나 키가
필요 없고 표준 443 포트로 접근할 수 있어서 GitHub Actions에서 바로 쓸 수
있다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
import requests

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def fetch_daily_ohlcv(symbol: str, start: date, end: date, timeout: float = 15.0) -> pd.DataFrame:
    """symbol은 야후 티커 그대로 쓴다(미국 ETF는 SPY, QQQ처럼 접미사가 없다)."""
    period1 = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())
    period2 = int(datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp()) + 86400

    response = requests.get(
        CHART_URL.format(symbol=symbol),
        params={"period1": period1, "period2": period2, "interval": "1d", "events": "history"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=timeout,
    )
    response.raise_for_status()
    return _parse_chart_response(response.json())


def _parse_chart_response(payload: dict) -> pd.DataFrame:
    columns = ["trade_date", "open", "high", "low", "close", "volume"]
    results = payload.get("chart", {}).get("result") or []
    if not results:
        return pd.DataFrame(columns=columns)

    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]

    df = pd.DataFrame(
        {
            "trade_date": [datetime.fromtimestamp(ts, tz=UTC).date() for ts in timestamps],
            "open": quote["open"],
            "high": quote["high"],
            "low": quote["low"],
            "close": quote["close"],
            "volume": quote["volume"],
        }
    )
    return df.dropna(subset=["close"]).reset_index(drop=True)
