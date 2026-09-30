"""국채 금리, 기준금리 같은 거시지표를 FRED에서 받아 구글 드라이브에 쌓는다.

update_prices.py와 완전히 같은 방식이다. 폴더 구조도 같다
(01_시세원본/<시리즈코드>/daily.csv). 종목 시세와 같은 루트 폴더 밑에
쌓아서, 대시보드의 목록·파일 조회 웹훅을 그대로 재사용한다(따로 만들지
않는다).

이미 받아 둔 것이 있으면 마지막 날짜 다음부터만 받아서 이어 붙인다.
--full-refresh를 켜면 처음부터 다시 받아 덮어쓴다(기본은 10년치, --start로
시작일을 직접 정하면 그 날부터).
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from datetime import UTC, date, datetime, timedelta

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from auto_trading.fred import fetch_daily_series
from auto_trading.gdrive import (
    _build_service,
    download_text,
    find_or_create_folder,
    upload_text,
)
from auto_trading.prices import merge_price_data

#: update_prices.py와 같은 01_시세원본 폴더. 종목 시세와 거시지표를 같은
#: 루트 밑에 두어야 시세 목록·시세 파일 웹훅이 둘 다 찾는다.
DEFAULT_PRICES_FOLDER_ID = "17RdksSi5F3kDh8GEgnZ2nu-ytYH-YW-o"
YEARS_OF_HISTORY = 10


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--series", required=True, help="쉼표로 구분한 FRED 시리즈 코드 (예: DGS10,FEDFUNDS)")
    parser.add_argument("--full-refresh", default="false", help="true면 처음부터 다시 받는다")
    parser.add_argument(
        "--start",
        default="",
        help="전체 재수집(--full-refresh true)일 때 시작일(YYYY-MM-DD). 비워 두면 오늘로부터 "
        f"{YEARS_OF_HISTORY}년 전부터 받는다.",
    )
    return parser.parse_args()


def _update_one(service, root_folder_id: str, series_id: str, full_refresh: bool, start_override: date | None) -> None:
    series_id = series_id.strip().upper()
    if not series_id:
        return
    folder_id = find_or_create_folder(service, root_folder_id, series_id)
    existing_text = None if full_refresh else download_text(service, folder_id, "daily.csv")
    today = datetime.now(UTC).date()

    if existing_text:
        existing = pd.read_csv(io.StringIO(existing_text), parse_dates=["trade_date"])
        existing["trade_date"] = existing["trade_date"].dt.date
        last_date = existing["trade_date"].max()
        start = last_date + timedelta(days=1)
        if start > today:
            print(f"{series_id}: 이미 최신입니다 (마지막 날짜 {last_date}).")
            return
        new_rows = fetch_daily_series(series_id, start, today)
        if new_rows.empty:
            print(f"{series_id}: 새로 받은 날짜가 없습니다 (마지막 날짜 {last_date}).")
            return
        combined = merge_price_data(existing, new_rows)
        print(f"{series_id}: {len(new_rows)}개 날짜를 새로 받았습니다 ({start} ~ {today}).")
    else:
        start = start_override or (today - timedelta(days=365 * YEARS_OF_HISTORY))
        combined = fetch_daily_series(series_id, start, today)
        if combined.empty:
            print(f"{series_id}: FRED에서 받은 데이터가 없습니다. 시리즈 코드를 확인하세요.")
            return
        print(f"{series_id}: 처음부터 {len(combined)}개 날짜를 받았습니다 ({start} ~ {today}).")

    upload_text(service, folder_id, "daily.csv", combined.to_csv(index=False))


def main() -> None:
    args = _parse_args()
    full_refresh = args.full_refresh.strip().lower() == "true"
    start_override = date.fromisoformat(args.start) if args.start.strip() else None
    root_folder_id = os.environ.get("GDRIVE_PRICES_FOLDER_ID", DEFAULT_PRICES_FOLDER_ID)

    service = _build_service()
    series_list = [s for s in args.series.split(",") if s.strip()]
    if not series_list:
        raise SystemExit("시리즈를 하나도 못 읽었습니다. --series를 확인하세요.")

    for series_id in series_list:
        _update_one(service, root_folder_id, series_id, full_refresh, start_override)


if __name__ == "__main__":
    main()
