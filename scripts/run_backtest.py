"""구글 드라이브에 쌓인 시세로 여러 매수 방식을 비교한다.

종목마다 01_시세원본/<종목>/daily.csv를 읽어서, 사용자가 고른 전략(최대
5개)을 각각 돌리고 마지막 날 기준 총투자금·실현손익·평가손익·합계를
표로 보여준다. 시계열 전체는 02_백테스트결과에 JSON으로 남겨서 화면이
다시 계산하지 않고 그래프를 그릴 수 있게 한다.

**전략 조건은 전부 --strategies로 받는 JSON 안에 있어야 한다.** 나눠
살 횟수, 매수 간격 같은 값을 이 스크립트가 대신 정하지 않는다. 대시보드
화면에서 사람이 입력한 값이 그대로 여기까지 와야 한다.

**--upload를 켜면 서식 있는 엑셀도 같이 남긴다**(2026-09-21에 더함).
종목·전략 조합마다 하나씩 02_백테스트결과/<종목>/ 아래에, 이번 실행
전체를 비교한 것 하나를 02_백테스트결과/종목비교결과/ 아래에 올린다.
파일 이름의 실행일자는 KST 날짜다. 같은 날 같은 조건으로 다시 올리면
그 파일을 덮어쓴다(daily.csv를 올릴 때와 같은 방식이다).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from auto_trading.backtest import build_strategy, run_backtest
from auto_trading.gdrive import _build_service, download_text, find_child, find_or_create_folder, upload_bytes, upload_text
from auto_trading.xlsx_report import build_comparison_report, build_symbol_report

PRICES_FOLDER_ID = "17RdksSi5F3kDh8GEgnZ2nu-ytYH-YW-o"  # 01_시세원본
RESULTS_FOLDER_ID = "1W9QQnstslExQCtBvphvoCJZ-b5y9nhvt"  # 02_백테스트결과
COMPARISON_SUBFOLDER = "종목비교결과"
XLSX_MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", required=True, help="쉼표로 구분한 티커 (예: SPY,QQQ)")
    parser.add_argument(
        "--strategies",
        required=True,
        help=(
            "비교할 전략 설정 JSON 배열(최대 5개). 각 항목은 key와 그 전략에 "
            '필요한 값을 담는다. 예: [{"key":"lump_sum"},'
            '{"key":"dca","label":"매일 적립","amount":100000,"interval_days":1,'
            '"take_profit_pct":0.3,"stop_loss_pct":0.1}]'
        ),
    )
    parser.add_argument("--capital", type=float, required=True, help="종목·전략마다 쓰는 총자본(원)")
    parser.add_argument("--start", required=True, help="조회 시작일 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="조회 종료일 YYYY-MM-DD")
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
    return df[(df["trade_date"] >= date.fromisoformat(start)) & (df["trade_date"] <= date.fromisoformat(end))].reset_index(
        drop=True
    )


def _safe_filename_part(text: str) -> str:
    text = re.sub(r'[\\/:*?"<>|]', "_", text.strip())
    text = re.sub(r"\s+", "_", text)
    return text or "값없음"


def _workbook_bytes(workbook) -> bytes:
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def main() -> None:
    args = _parse_args()
    try:
        strategy_configs = json.loads(args.strategies)
    except json.JSONDecodeError as e:
        raise SystemExit(f"--strategies가 올바른 JSON이 아닙니다: {e}") from e
    if not isinstance(strategy_configs, list) or not strategy_configs:
        raise SystemExit("--strategies는 전략 설정이 하나 이상 담긴 JSON 배열이어야 합니다.")
    if len(strategy_configs) > 5:
        raise SystemExit(f"전략은 최대 5개까지 비교합니다 ({len(strategy_configs)}개 받음).")

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    service = _build_service()
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    generated_at_text = now_kst.strftime("%Y-%m-%d %H:%M:%S")
    run_date_slug = now_kst.strftime("%Y%m%d")
    period_slug = f"{args.start.replace('-', '')}-{args.end.replace('-', '')}"

    summary_rows = []
    series_by_key: dict[str, list[dict]] = {}

    for symbol in symbols:
        prices = _filter_range(_load_prices(service, symbol), args.start, args.end)
        if prices.empty:
            print(f"{symbol}: 이 구간({args.start}~{args.end})에 시세가 없습니다. 건너뜁니다.")
            continue

        for config in strategy_configs:
            strategy = build_strategy(config, args.capital)
            result = run_backtest(prices, capital=args.capital, strategy=strategy)
            last = result.iloc[-1]
            summary_row = {
                "symbol": symbol,
                "strategy_key": strategy.key,
                "strategy_name": strategy.name,
                "총투자금": round(last["invested_cumulative"]),
                "실현손익": round(last["realized_pnl"]),
                "평가손익": round(last["unrealized_pnl"]),
                "합계": round(last["total_pnl"]),
                "수익률": round(last["total_pnl"] / args.capital * 100, 2),
            }
            summary_rows.append(summary_row)
            series_key = f"{symbol}:{strategy.key}"
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

            if args.upload:
                symbol_folder_id = find_or_create_folder(service, RESULTS_FOLDER_ID, symbol)
                condition_slug = _safe_filename_part(strategy.name)
                xlsx_name = f"{run_date_slug}_{symbol}_{period_slug}_{condition_slug}.xlsx"
                workbook = build_symbol_report(
                    symbol=symbol,
                    strategy_name=strategy.name,
                    capital=args.capital,
                    start=args.start,
                    end=args.end,
                    generated_at_kst=generated_at_text,
                    summary_row=summary_row,
                    daily=result,
                )
                upload_bytes(service, symbol_folder_id, xlsx_name, _workbook_bytes(workbook), XLSX_MIMETYPE)

    summary = pd.DataFrame(summary_rows)
    if summary.empty:
        raise SystemExit("계산된 것이 없습니다.")
    print(summary.to_string(index=False))

    if args.upload:
        payload = {
            "생성시각_KST": generated_at_text,
            "종목": symbols,
            "전략설정": strategy_configs,
            "자본금": args.capital,
            "조회기간": {"시작": args.start, "종료": args.end},
            "요약": summary_rows,
            "시계열": series_by_key,
        }
        json_filename = f"비교_{now_kst.strftime('%Y%m%d_%H%M%S')}_{'-'.join(symbols)}.json"
        upload_text(service, RESULTS_FOLDER_ID, json_filename, json.dumps(payload, ensure_ascii=False, indent=2))

        comparison_folder_id = find_or_create_folder(service, RESULTS_FOLDER_ID, COMPARISON_SUBFOLDER)
        symbols_slug = "-".join(symbols)
        comparison_xlsx_name = f"{run_date_slug}_{symbols_slug}_{period_slug}.xlsx"
        comparison_workbook = build_comparison_report(
            symbols=symbols,
            capital=args.capital,
            start=args.start,
            end=args.end,
            generated_at_kst=generated_at_text,
            summary_rows=summary_rows,
        )
        upload_bytes(
            service, comparison_folder_id, comparison_xlsx_name, _workbook_bytes(comparison_workbook), XLSX_MIMETYPE
        )


if __name__ == "__main__":
    main()
