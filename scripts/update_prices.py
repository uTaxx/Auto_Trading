"""ETF 일봉 시세를 야후에서 받아 구글 드라이브에 종목별로 쌓는다.

폴더 구조: 01_시세원본/<종목코드>/daily.csv

이미 받아 둔 것이 있으면 마지막 날짜 다음부터만 받아서 이어 붙인다.
--full-refresh를 켜면 10년치를 처음부터 다시 받아 덮어쓴다.

**요청한 종목과 같이 미국 달러/원화 환율(KRW=X)도 항상 같이 받는다**
(2026-09-21에 더함). 화면에서 값을 원화로 환산해 보여주려면 날짜별
환율이 있어야 한다. 새 파이프라인을 따로 만들지 않고, 종목 시세와
같은 방식(01_시세원본/KRW=X/daily.csv)으로 야후에서 받아 쌓는다.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from datetime import UTC, datetime, timedelta

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from auto_trading.gdrive import (
    _build_service,
    download_text,
    find_or_create_folder,
    upload_text,
)
from auto_trading.prices import merge_price_data
from auto_trading.yahoo import fetch_daily_ohlcv

#: 01_시세원본 폴더 ID. 폴더 ID는 비밀값이 아니다(권한이 없으면 접근 자체가
#: 안 된다). 그래서 시크릿이 아니라 코드에 상수로 둔다.
DEFAULT_PRICES_FOLDER_ID = "17RdksSi5F3kDh8GEgnZ2nu-ytYH-YW-o"
YEARS_OF_HISTORY = 10
FX_SYMBOL = "KRW=X"  # 미국 달러 대비 원화. 화면의 달러/원화 전환 그래프가 쓴다.


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", required=True, help="쉼표로 구분한 티커 (예: SPY,QQQ)")
    parser.add_argument("--full-refresh", default="false", help="true면 10년치를 처음부터 다시 받는다")
    return parser.parse_args()


def _update_one(service, root_folder_id: str, symbol: str, full_refresh: bool) -> None:
    symbol = symbol.strip().upper()
    if not symbol:
        return
    folder_id = find_or_create_folder(service, root_folder_id, symbol)
    existing_text = None if full_refresh else download_text(service, folder_id, "daily.csv")
    today = datetime.now(UTC).date()

    if existing_text:
        existing = pd.read_csv(io.StringIO(existing_text), parse_dates=["trade_date"])
        existing["trade_date"] = existing["trade_date"].dt.date
        last_date = existing["trade_date"].max()
        start = last_date + timedelta(days=1)
        if start > today:
            print(f"{symbol}: 이미 최신입니다 (마지막 날짜 {last_date}).")
            return
        new_rows = fetch_daily_ohlcv(symbol, start, today)
        if new_rows.empty:
            print(f"{symbol}: 새로 받은 거래일이 없습니다 (마지막 날짜 {last_date}).")
            return
        combined = merge_price_data(existing, new_rows)
        print(f"{symbol}: {len(new_rows)}개 거래일을 새로 받았습니다 ({start} ~ {today}).")
    else:
        start = today - timedelta(days=365 * YEARS_OF_HISTORY)
        combined = fetch_daily_ohlcv(symbol, start, today)
        if combined.empty:
            print(f"{symbol}: 야후에서 받은 데이터가 없습니다. 티커를 확인하세요.")
            return
        print(f"{symbol}: 처음부터 {len(combined)}개 거래일을 받았습니다 ({start} ~ {today}).")

    upload_text(service, folder_id, "daily.csv", combined.to_csv(index=False))


def main() -> None:
    args = _parse_args()
    full_refresh = args.full_refresh.strip().lower() == "true"
    root_folder_id = os.environ.get("GDRIVE_PRICES_FOLDER_ID", DEFAULT_PRICES_FOLDER_ID)

    service = _build_service()
    symbols = [s for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        raise SystemExit("종목을 하나도 못 읽었습니다. --symbols를 확인하세요.")

    for symbol in symbols:
        _update_one(service, root_folder_id, symbol, full_refresh)

    _update_one(service, root_folder_id, FX_SYMBOL, full_refresh)


if __name__ == "__main__":
    main()
