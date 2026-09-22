"""여러 종목이 현금 하나를 나눠 쓰면서 같이 매매하는 포트폴리오
백테스트.

`run_backtest.py`는 종목마다 총자본을 전부 가진 것처럼 따로 계산한다.
이 스크립트는 그 대신 총자본 하나를 모든 종목이 같이 쓴다. 한 종목이
현금을 많이 쓰면 같은 날 다른 종목은 그만큼 못 사는 것까지 반영해서,
"여러 종목을 같이 들고 가는 실제 계좌"에 더 가깝게 계산한다.

**종목 순서가 결과에 영향을 준다.** --symbols에 적은 순서대로 같은 날
현금을 먼저 쓴다. --strategies는 종목마다 다른 매수 방식·익절·손절을
줄 수 있는 JSON 객체다(키가 종목, 값이 find_best_strategy.py의
--search 한 조합과 같은 모양의 전략 설정 하나).

결과는 화면 4번(결과 조회)이 그대로 읽을 수 있도록
`포트폴리오_실행시각_종목1-종목2.json`으로 02_백테스트결과에 남긴다.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from auto_trading.backtest import build_strategy
from auto_trading.gdrive import (
    _build_service,
    find_or_create_folder,
    next_result_number,
    upload_bytes,
    upload_text,
)
from auto_trading.portfolio import run_portfolio_backtest, summarize_portfolio_result
from auto_trading.prices_io import filter_range, load_prices
from auto_trading.xlsx_report import build_portfolio_report

RESULTS_FOLDER_ID = "1W9QQnstslExQCtBvphvoCJZ-b5y9nhvt"  # 02_백테스트결과
COMPARISON_SUBFOLDER = "종목비교결과"
XLSX_MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", required=True, help="쉼표로 구분한 티커, 적은 순서대로 현금을 먼저 쓴다 (예: SOXL,TQQQ)")
    parser.add_argument(
        "--strategies",
        required=True,
        help=(
            "종목마다 다른 전략 설정을 담은 JSON 객체. 키는 종목, 값은 "
            'find_best_strategy.py --search 한 조합과 같은 모양. 예: '
            '{"SOXL":{"key":"lump_sum"},"TQQQ":{"key":"dca","amount":100000}}'
        ),
    )
    parser.add_argument("--capital", type=float, required=True, help="포트폴리오 전체가 나눠 쓰는 총자본(원)")
    parser.add_argument("--start", required=True, help="조회 시작일 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="조회 종료일 YYYY-MM-DD")
    parser.add_argument("--upload", action="store_true", help="결과를 02_백테스트결과에 올린다")
    return parser.parse_args()


def _workbook_bytes(workbook) -> bytes:
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def main() -> None:
    args = _parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        raise SystemExit("--symbols에 종목을 하나 이상 적어야 합니다.")

    try:
        strategy_configs = json.loads(args.strategies)
    except json.JSONDecodeError as e:
        raise SystemExit(f"--strategies가 올바른 JSON이 아닙니다: {e}") from e
    if not isinstance(strategy_configs, dict):
        raise SystemExit("--strategies는 종목별 전략 설정을 담은 JSON 객체여야 합니다.")
    missing = [s for s in symbols if s not in strategy_configs]
    if missing:
        raise SystemExit(f"--strategies에 다음 종목의 전략 설정이 없습니다: {', '.join(missing)}")

    service = _build_service()
    prices_by_symbol = {}
    for symbol in symbols:
        prices = filter_range(load_prices(service, symbol), args.start, args.end)
        if prices.empty:
            raise SystemExit(f"{symbol}: 이 구간({args.start}~{args.end})에 시세가 없습니다.")
        prices_by_symbol[symbol] = prices

    strategy_by_symbol = {
        symbol: build_strategy(strategy_configs[symbol], args.capital) for symbol in symbols
    }

    daily, breakdown = run_portfolio_backtest(prices_by_symbol, args.capital, strategy_by_symbol)
    summary = summarize_portfolio_result(daily, args.capital)

    print(f"{'종목':<10} {'총투자금':>16} {'실현손익':>14} {'평가손익':>14} {'합계':>14}")
    for symbol in symbols:
        row = breakdown[symbol]
        print(f"{symbol:<10} {row['총투자금']:>16,} {row['실현손익']:>14,} {row['평가손익']:>14,} {row['합계']:>14,}")
    print(
        f"\n포트폴리오 전체 수익률 {summary['수익률']:.2f}%, 최대낙폭 {summary['최대낙폭']:.2f}%, "
        f"현금부족일수 {summary['현금부족일수']}일(부족금액 합계 {summary['현금부족금액']:,}원)"
    )
    if summary["현금부족일수"] > 0:
        print(
            "(안내) 현금이 모자라 설정값대로 못 산 날이 있습니다. 종목 순서를 바꾸거나 "
            "총자본을 늘리면 결과가 달라질 수 있습니다."
        )

    if args.upload:
        now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
        generated_at_text = now_kst.strftime("%Y-%m-%d %H:%M:%S")
        run_date_slug = now_kst.strftime("%Y%m%d")
        period_slug = f"{args.start.replace('-', '')}-{args.end.replace('-', '')}"
        symbols_slug = "-".join(symbols)

        comparison_folder_id = find_or_create_folder(service, RESULTS_FOLDER_ID, COMPARISON_SUBFOLDER)
        xlsx_name = f"{run_date_slug}_포트폴리오_{symbols_slug}_{period_slug}.xlsx"
        workbook = build_portfolio_report(
            symbols=symbols,
            capital=args.capital,
            start=args.start,
            end=args.end,
            generated_at_kst=generated_at_text,
            summary=summary,
            symbol_breakdown=breakdown,
            daily=daily,
        )
        xlsx_file_id = upload_bytes(service, comparison_folder_id, xlsx_name, _workbook_bytes(workbook), XLSX_MIMETYPE)

        결과번호 = next_result_number(service, RESULTS_FOLDER_ID)
        payload = {
            "결과번호": 결과번호,
            "생성시각_KST": generated_at_text,
            "종목": symbols,
            "종목순서_참고": "같은 날 현금이 모자라면 이 배열 순서대로 먼저 쓴다",
            "전략설정": strategy_configs,
            "자본금": args.capital,
            "조회기간": {"시작": args.start, "종료": args.end},
            "포트폴리오_요약": summary,
            "종목별_요약": breakdown,
            "포트폴리오_시계열": [
                {
                    "trade_date": str(row["trade_date"]),
                    "cash": round(row["cash"]),
                    "market_value": round(row["market_value"]),
                    "total_value": round(row["total_value"]),
                    "buy_amount": round(row["buy_amount"]),
                    "buy_shortfall": round(row["buy_shortfall"]),
                    "sell_amount": round(row["sell_amount"]),
                }
                for _, row in daily.iterrows()
            ],
            "비교엑셀": {"file_id": xlsx_file_id, "이름": xlsx_name},
        }
        filename = f"{결과번호:04d}_포트폴리오_{now_kst.strftime('%Y%m%d_%H%M%S')}_{symbols_slug}.json"
        upload_text(service, RESULTS_FOLDER_ID, filename, json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"결과번호 {결과번호}로 저장했습니다.")


if __name__ == "__main__":
    main()
