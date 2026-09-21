"""구글 드라이브에 쌓인 시세로 여러 매수 방식을 비교한다.

종목마다 01_시세원본/<종목>/daily.csv를 읽어서, 고른 전략(최대 5개)을
각각 돌리고 마지막 날 기준 총투자금·실현손익·평가손익·합계를 표로
보여준다. 시계열 전체는 02_백테스트결과에 JSON으로 남겨서 나중에
그래프로 그릴 때 다시 계산하지 않아도 되게 한다.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from auto_trading.backtest import STRATEGY_FACTORIES, run_backtest
from auto_trading.gdrive import _build_service, download_text, find_child, upload_text

PRICES_FOLDER_ID = "17RdksSi5F3kDh8GEgnZ2nu-ytYH-YW-o"  # 01_시세원본
RESULTS_FOLDER_ID = "1W9QQnstslExQCtBvphvoCJZ-b5y9nhvt"  # 02_백테스트결과


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", required=True, help="쉼표로 구분한 티커 (예: SPY,QQQ)")
    parser.add_argument(
        "--strategies",
        default=",".join(STRATEGY_FACTORIES.keys()),
        help=f"쉼표로 구분한 전략 키. 고를 수 있는 것: {', '.join(STRATEGY_FACTORIES.keys())}",
    )
    parser.add_argument("--capital", type=float, default=10_000_000, help="종목·전략마다 쓰는 총자본(원)")
    parser.add_argument("--start", default="", help="시작일 YYYY-MM-DD (비우면 받아 둔 시세 전체)")
    parser.add_argument("--end", default="", help="종료일 YYYY-MM-DD (비우면 받아 둔 시세 전체)")
    parser.add_argument("--upload", action="store_true", help="결과를 02_백테스트결과에 올린다")
    return parser.parse_args()


def _load_prices(service, symbol: str) -> pd.DataFrame:
    symbol = symbol.strip().upper()
    folder_id = find_child(service, PRICES_FOLDER_ID, symbol, folder_only=True)
    text = download_text(service, folder_id, "daily.csv") if folder_id else None
    if text is None:
        raise SystemExit(f"{symbol}: 시세가 없습니다. 대시보드에서 먼저 받아 두세요.")
    df = pd.read_csv(io.StringIO(text), parse_dates=["trade_date"])
    df["trade_date"] = df["trade_date"].dt.date
    return df


def _filter_range(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    if start:
        df = df[df["trade_date"] >= date.fromisoformat(start)]
    if end:
        df = df[df["trade_date"] <= date.fromisoformat(end)]
    return df.reset_index(drop=True)


def main() -> None:
    args = _parse_args()
    strategy_keys = [k.strip() for k in args.strategies.split(",") if k.strip()]
    if len(strategy_keys) > 5:
        raise SystemExit(f"전략은 최대 5개까지 비교합니다 ({len(strategy_keys)}개 받음).")
    unknown = [k for k in strategy_keys if k not in STRATEGY_FACTORIES]
    if unknown:
        raise SystemExit(f"모르는 전략 키: {unknown}. 고를 수 있는 것: {list(STRATEGY_FACTORIES.keys())}")

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    service = _build_service()

    summary_rows = []
    series_by_key: dict[str, list[dict]] = {}

    for symbol in symbols:
        prices = _filter_range(_load_prices(service, symbol), args.start, args.end)
        if prices.empty:
            print(f"{symbol}: 이 구간에 시세가 없습니다. 건너뜁니다.")
            continue

        for key in strategy_keys:
            strategy = STRATEGY_FACTORIES[key](args.capital)
            result = run_backtest(prices, capital=args.capital, strategy=strategy)
            last = result.iloc[-1]
            summary_rows.append(
                {
                    "symbol": symbol,
                    "strategy_key": key,
                    "strategy_name": strategy.name,
                    "총투자금": round(last["invested_cumulative"]),
                    "실현손익": round(last["realized_pnl"]),
                    "평가손익": round(last["unrealized_pnl"]),
                    "합계": round(last["total_pnl"]),
                    "수익률": round(last["total_pnl"] / args.capital * 100, 2),
                }
            )
            series_key = f"{symbol}:{key}"
            series_by_key[series_key] = [
                {
                    "trade_date": str(row["trade_date"]),
                    "total_value": round(row["total_value"]),
                    "invested_cumulative": round(row["invested_cumulative"]),
                    "realized_pnl": round(row["realized_pnl"]),
                    "unrealized_pnl": round(row["unrealized_pnl"]),
                }
                for _, row in result.iterrows()
            ]

    summary = pd.DataFrame(summary_rows)
    if summary.empty:
        raise SystemExit("계산된 것이 없습니다.")
    print(summary.to_string(index=False))

    if args.upload:
        payload = {
            "생성시각_KST": datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S"),
            "종목": symbols,
            "전략": strategy_keys,
            "자본금": args.capital,
            "요약": summary_rows,
            "시계열": series_by_key,
        }
        filename = f"비교_{'-'.join(symbols)}_{'-'.join(strategy_keys)}.json"
        upload_text(service, RESULTS_FOLDER_ID, filename, json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
