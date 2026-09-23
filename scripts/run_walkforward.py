"""Walk-forward(학습·검증 롤링) 방식으로 최적 조건을 찾는다.

전체 기간에서 가장 수익률이 높았던 조건 하나를 뽑는 기존 방식
(find_best_strategy.py)과 달리, 시세를 학습기간(In-Sample)과 검증기간
(Out-of-Sample)으로 나눈다. 학습기간에서 찾은 조건을 검증기간의
데이터는 전혀 보지 않은 채로 그대로 적용해서 성과가 유지되는지 본다.
학습·검증 기간 길이와 이동 간격은 --in-sample-years/--out-sample-years/
--step-years로 받는다(기본값 3년/1년/1년일 뿐, 하드코딩된 최적값이
아니다).

--start/--end를 비워 두면 구글 드라이브에 받아 둔 시세 전체를 쓴다
("가능하면 7~10년 이상의 과거 데이터를 활용"이라는 요청을 따른 것으로,
데이터가 있는 만큼 폴드를 만들고 모자라면 오류로 알린다).

비교할 수 있도록, 같은 종목·같은 검색 조건으로 기존 방식(전체 기간
최적화, 1등만 고르는 방식)도 같이 계산해서 결과에 남긴다. 두 결과가
많이 다르면(전체 기간 1등이 Walk-forward 검증에서는 안 뽑혔다면)
전체 기간 1등이 과최적화됐을 가능성이 있다는 뜻이다.

결과는 02_백테스트결과에 `워크포워드_실행시각_종목명.json`으로 남긴다.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from auto_trading.backtest import build_strategy, run_backtest
from auto_trading.gdrive import (
    _build_service,
    find_or_create_folder,
    next_result_number,
    upload_bytes,
    upload_text,
)
from auto_trading.metrics import extended_metrics
from auto_trading.optimize import build_strategy_configs, run_search
from auto_trading.prices_io import filter_range, load_prices
from auto_trading.walkforward import WalkForwardConfig, build_summary_stats, run_walk_forward
from auto_trading.xlsx_report import build_comparison_report

RESULTS_FOLDER_ID = "1W9QQnstslExQCtBvphvoCJZ-b5y9nhvt"  # 02_백테스트결과
COMPARISON_SUBFOLDER = "종목비교결과"
XLSX_MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, help="종목 하나 (예: SOXL)")
    parser.add_argument("--capital", type=float, required=True, help="총자본(원)")
    parser.add_argument(
        "--search",
        required=True,
        help='전략 키 -> 변수별 후보값 목록 JSON. find_best_strategy.py의 --search와 같은 모양이다.',
    )
    parser.add_argument("--start", default="", help="조회 시작일 YYYY-MM-DD. 비워 두면 받아 둔 시세의 첫날부터.")
    parser.add_argument("--end", default="", help="조회 종료일 YYYY-MM-DD. 비워 두면 받아 둔 시세의 마지막날까지.")
    parser.add_argument("--in-sample-years", type=float, default=3.0, help="학습기간(년). 기본 3년.")
    parser.add_argument("--out-sample-years", type=float, default=1.0, help="검증기간(년). 기본 1년.")
    parser.add_argument("--step-years", type=float, default=1.0, help="다음 폴드로 이동하는 간격(년). 기본 1년.")
    parser.add_argument("--upload", action="store_true", help="결과를 02_백테스트결과에 올린다")
    return parser.parse_args()


def _workbook_bytes(workbook) -> bytes:
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def main() -> None:
    args = _parse_args()
    symbol = args.symbol.strip().upper()

    try:
        search = json.loads(args.search)
    except json.JSONDecodeError as e:
        raise SystemExit(f"--search가 올바른 JSON이 아닙니다: {e}") from e
    if not isinstance(search, dict) or not search:
        raise SystemExit("--search는 전략 키마다 후보값을 담은 JSON 객체여야 합니다.")

    wf_config = WalkForwardConfig(
        in_sample_years=args.in_sample_years,
        out_sample_years=args.out_sample_years,
        step_years=args.step_years,
    )

    service = _build_service()
    prices_all = load_prices(service, symbol)
    if args.start and args.end:
        prices = filter_range(prices_all, args.start, args.end)
    else:
        prices = prices_all.sort_values("trade_date").reset_index(drop=True)
    if prices.empty:
        raise SystemExit(f"{symbol}: 계산할 시세가 없습니다.")

    period_start = str(prices["trade_date"].min())
    period_end = str(prices["trade_date"].max())
    print(f"시세 범위: {period_start} ~ {period_end} ({len(prices)}거래일)")

    wf_result = run_walk_forward(symbol, prices, args.capital, search, wf_config)

    print(f"\n폴드 {len(wf_result['폴드별_결과'])}개")
    print(f"{'폴드':<4} {'검증기간':<23} {'선정조건':<45} {'검증 수익률':>10} {'검증 최대낙폭':>10}")
    for fold in wf_result["폴드별_결과"]:
        period = f"{fold['검증기간']['시작']}~{fold['검증기간']['종료']}"
        perf = fold["검증기간_성과"]
        print(
            f"{fold['폴드']:<4} {period:<23} {fold['선정조건']['설명']:<45} "
            f"{perf['누적수익률']:>9.2f}% {perf['최대낙폭']:>9.2f}%"
        )
    combined = wf_result["전체_OOS_성과"]
    print(
        f"\n전체 OOS(검증기간을 이어 붙인 것) 누적수익률 {combined['누적수익률']:.2f}%, "
        f"CAGR {combined['CAGR']}, 최대낙폭 {combined['최대낙폭']:.2f}%, "
        f"Calmar {combined['Calmar']}, 거래횟수 {combined['거래횟수']}"
    )

    # 비교용: 기존 방식(같은 종목·같은 검색 조건을 전체 기간에서 한 번에
    # 최적화). 전체 기간 1등이 Walk-forward에서 반복해서 뽑히지 않았다면
    # 과최적화 가능성이 있다는 뜻이라, 나란히 남긴다.
    configs = build_strategy_configs(search)
    baseline_rows = run_search(symbol, prices, args.capital, configs)
    print(f"\n(비교) 전체 기간 최적화 1위: {baseline_rows[0]['strategy_name']} ({baseline_rows[0]['수익률']:.2f}%)")

    # 비교 그래프용: 전체 기간 최적화 1위의 일별 총자산도 남긴다.
    # run_search는 요약 한 줄만 내고 일별 값은 버리므로, 같은 조건으로
    # 한 번 더 돌린다(라벨이 조합마다 고유해서 결과 줄과 설정을 다시
    # 짝지을 수 있다).
    config_by_label = {c["label"]: c for c in configs}
    best_config = config_by_label[baseline_rows[0]["strategy_name"]]
    best_baseline_strategy = build_strategy(best_config, args.capital)
    best_baseline_result = run_backtest(prices, capital=args.capital, strategy=best_baseline_strategy)
    baseline_series = [
        {
            "trade_date": str(row["trade_date"]),
            "total_value": round(row["total_value"]),
            "buy_amount": round(row["buy_amount"]) if row["buy_amount"] else 0,
            "sell_amount": round(row["sell_amount"]) if row["sell_amount"] else 0,
            "sell_type": row["sell_type"] if pd.notna(row["sell_type"]) else None,
        }
        for _, row in best_baseline_result.iterrows()
    ]
    # 비교표에 CAGR·Calmar·거래횟수까지 나란히 보여주려면 baseline_rows
    # (summarize_result 결과, 수익률·최대낙폭만 있다)만으로는 모자라서
    # 위에서 만든 일별 결과로 한 번 더 계산한다.
    baseline_metrics = extended_metrics(best_baseline_result, args.capital)

    summary_stats = build_summary_stats(wf_result["폴드별_결과"])
    if summary_stats["폴드수경고"]:
        print(f"\n(안내) {summary_stats['폴드수경고']}")
    print(f"(안내) 전략 변경 {summary_stats['전략변경횟수']}회, OOS 양수 폴드 {summary_stats['OOS_폴드수']['양수']}개, 음수 폴드 {summary_stats['OOS_폴드수']['음수']}개")

    if args.upload:
        now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
        generated_at_text = now_kst.strftime("%Y-%m-%d %H:%M:%S")
        run_date_slug = now_kst.strftime("%Y%m%d")

        comparison_folder_id = find_or_create_folder(service, RESULTS_FOLDER_ID, COMPARISON_SUBFOLDER)
        comparison_xlsx_name = f"{run_date_slug}_워크포워드_{symbol}_{period_start.replace('-', '')}-{period_end.replace('-', '')}.xlsx"
        comparison_workbook = build_comparison_report(
            symbols=[symbol],
            capital=args.capital,
            start=period_start,
            end=period_end,
            generated_at_kst=generated_at_text,
            summary_rows=baseline_rows,
        )
        comparison_file_id = upload_bytes(
            service, comparison_folder_id, comparison_xlsx_name, _workbook_bytes(comparison_workbook), XLSX_MIMETYPE
        )

        결과번호 = next_result_number(service, RESULTS_FOLDER_ID)
        payload = {
            "결과번호": 결과번호,
            "생성시각_KST": generated_at_text,
            "종목": [symbol],
            "검색조건": search,
            "자본금": args.capital,
            "조회기간": {"시작": period_start, "종료": period_end},
            "워크포워드_설정": {
                "학습기간_년": args.in_sample_years,
                "검증기간_년": args.out_sample_years,
                "이동간격_년": args.step_years,
            },
            "폴드별_결과": wf_result["폴드별_결과"],
            "전체_OOS_성과": wf_result["전체_OOS_성과"],
            "전체_OOS_시계열": wf_result["전체_OOS_시계열"],
            "비교_전체기간_최적화": baseline_rows,
            "비교_전체기간_최적화_시계열": baseline_series,
            "비교_전체기간_최적화_지표": baseline_metrics,
            "요약통계": summary_stats,
            "비교엑셀": {"file_id": comparison_file_id, "이름": comparison_xlsx_name},
        }
        filename = f"{결과번호:04d}_워크포워드_{now_kst.strftime('%Y%m%d_%H%M%S')}_{symbol}.json"
        upload_text(service, RESULTS_FOLDER_ID, filename, json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"결과번호 {결과번호}로 저장했습니다.")


if __name__ == "__main__":
    main()
