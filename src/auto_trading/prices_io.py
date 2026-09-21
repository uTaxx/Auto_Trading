"""구글 드라이브에서 종목 시세를 읽어 오는 공통 함수.

run_backtest.py와 find_best_strategy.py가 같이 쓴다. 한 곳에만 있어야
두 스크립트가 서로 다른 방식으로 시세를 읽는 일이 안 생긴다.
"""

from __future__ import annotations

import io
from datetime import date

import pandas as pd

from .gdrive import download_text, find_child

PRICES_FOLDER_ID = "17RdksSi5F3kDh8GEgnZ2nu-ytYH-YW-o"  # 01_시세원본


def load_prices(service, symbol: str) -> pd.DataFrame:
    symbol = symbol.strip().upper()
    folder_id = find_child(service, PRICES_FOLDER_ID, symbol, folder_only=True)
    text = download_text(service, folder_id, "daily.csv") if folder_id else None
    if text is None:
        raise SystemExit(f"{symbol}: 시세가 없습니다. 대시보드에서 먼저 받아 두세요.")
    df = pd.read_csv(io.StringIO(text), parse_dates=["trade_date"])
    df["trade_date"] = df["trade_date"].dt.date
    return df


def filter_range(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    return df[
        (df["trade_date"] >= date.fromisoformat(start)) & (df["trade_date"] <= date.fromisoformat(end))
    ].reset_index(drop=True)
