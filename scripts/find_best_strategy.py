"""네 가지 매수 방식의 여러 변수 조합을 한꺼번에 계산해서, 수익률이
가장 좋은 조합을 찾는다.

종목 하나, 총자본, 조회기간만 받는다. 전략별 변수는 --search로 받는
후보값 목록(각 값을 하나씩 다 시험한다)을 쓴다. 익절선·손절선도
--take-profit-pct/--stop-loss-pct에 쉼표로 구분한 후보값 목록으로 받아서
다른 변수와 똑같이 조합에 곱한다(2026-09-21에 값 하나만 받던 것에서
바꿨다). 사람이 값을 하나씩 넣지 않아도 되지만, 그 후보 목록 자체는
화면에 미리 채워져 있고 사람이 바꿀 수 있다(자세한 규칙은
auto_trading/optimize.py를 본다).

결과는 화면 4번(결과 조회)이 그대로 읽을 수 있도록 run_backtest.py와
같은 모양의 JSON으로 02_백테스트결과에 남긴다. 1등만 남기지 않고 계산한
조합 전부를 수익률 내림차순으로 남긴다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from auto_trading.gdrive import _build_service, upload_text
from auto_trading.optimize import MAX_COMBINATIONS, build_strategy_configs, run_search
from auto_trading.prices_io import filter_range, load_prices

RESULTS_FOLDER_ID = "1W9QQnstslExQCtBvphvoCJZ-b5y9nhvt"  # 02_백테스트결과


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, help="종목 하나 (예: SOXL)")
    parser.add_argument("--capital", type=float, required=True, help="총자본(원)")
    parser.add_argument("--start", required=True, help="조회 시작일 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="조회 종료일 YYYY-MM-DD")
    parser.add_argument(
        "--search",
        required=True,
        help=(
            "전략 키 -> 변수별 후보값 목록을 담은 JSON. 예: "
            '{"lump_sum":{},"dca":{"amount":[100000,200000],"interval_days":[1,5]}}'
        ),
    )
    parser.add_argument(
        "--take-profit-pct",
        default=None,
        help="시험할 익절선 후보(비율, 쉼표로 구분, 예: 0.1,0.2,0.3). 안 주면 그 조건 없이 계산",
    )
    parser.add_argument(
        "--stop-loss-pct",
        default=None,
        help="시험할 손절선 후보(비율, 쉼표로 구분). 안 주면 그 조건 없이 계산",
    )
    parser.add_argument("--upload", action="store_true", help="결과를 02_백테스트결과에 올린다")
    return parser.parse_args()


def _parse_float_list(text: str | None) -> list[float] | None:
    if not text:
        return None
    values: list[float] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.append(float(part))
        except ValueError as e:
            raise SystemExit(f"'{part}'는 올바른 숫자가 아닙니다: {e}") from e
    return values or None


def main() -> None:
    args = _parse_args()
    symbol = args.symbol.strip().upper()

    try:
        search = json.loads(args.search)
    except json.JSONDecodeError as e:
        raise SystemExit(f"--search가 올바른 JSON이 아닙니다: {e}") from e
    if not isinstance(search, dict) or not search:
        raise SystemExit("--search는 전략 키마다 후보값을 담은 JSON 객체여야 합니다.")

    take_profit_candidates = _parse_float_list(args.take_profit_pct)
    stop_loss_candidates = _parse_float_list(args.stop_loss_pct)
    configs = build_strategy_configs(search, take_profit_candidates, stop_loss_candidates)
    print(f"조합 {len(configs)}개를 계산합니다(최대 {MAX_COMBINATIONS}개까지 허용).")

    service = _build_service()
    prices = filter_range(load_prices(service, symbol), args.start, args.end)
    if prices.empty:
        raise SystemExit(f"{symbol}: 이 구간({args.start}~{args.end})에 시세가 없습니다.")

    rows = run_search(symbol, prices, args.capital, configs)

    print(f"{'전략':<60} {'수익률':>10} {'최대낙폭':>10}")
    for row in rows[:10]:
        print(f"{row['strategy_name']:<60} {row['수익률']:>9.2f}% {row['최대낙폭']:>9.2f}%")
    if len(rows) > 10:
        print(f"...(나머지 {len(rows) - 10}개 생략, 전체는 업로드된 파일에서 확인)")

    if args.upload:
        now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
        payload = {
            "생성시각_KST": now_kst.strftime("%Y-%m-%d %H:%M:%S"),
            "종목": [symbol],
            "검색조건": search,
            "익절선_후보": take_profit_candidates,
            "손절선_후보": stop_loss_candidates,
            "자본금": args.capital,
            "조회기간": {"시작": args.start, "종료": args.end},
            "요약": rows,
            "시계열": {},
        }
        filename = f"최적화_{now_kst.strftime('%Y%m%d_%H%M%S')}_{symbol}.json"
        upload_text(service, RESULTS_FOLDER_ID, filename, json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
