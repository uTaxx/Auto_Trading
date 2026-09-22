from datetime import date, timedelta

import pandas as pd

from auto_trading.backtest import LumpSum, Strategy, run_backtest
from auto_trading.portfolio import run_portfolio_backtest, summarize_portfolio_result
from auto_trading.xlsx_report import build_comparison_report, build_portfolio_report, build_symbol_report


def _prices(closes: list[float]) -> pd.DataFrame:
    start = date(2024, 1, 2)
    return pd.DataFrame(
        {
            "trade_date": [start + timedelta(days=i) for i in range(len(closes))],
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1000] * len(closes),
        }
    )


def test_종목_보고서는_요약과_일별_시계열_두_시트를_만든다():
    prices = _prices([100.0, 110.0, 120.0])
    strategy = Strategy(key="lump_sum", name="일회 매수", buy_plan=LumpSum(1_000_000))
    result = run_backtest(prices, capital=1_000_000, strategy=strategy)
    summary_row = {
        "symbol": "SPY",
        "strategy_key": "lump_sum",
        "strategy_name": "일회 매수",
        "매수방식": "일회 매수",
        "매수금액": 1_000_000,
        "매수빈도": None,
        "이동평균조건": None,
        "등락구간": None,
        "익절선": None,
        "손절선": None,
        "총투자금": 1_000_000,
        "실현손익": 0,
        "평가손익": 200_000,
        "합계": 200_000,
        "수익률": 20.0,
        "최대낙폭": -5.0,
        "현금부족일수": 0,
    }

    wb = build_symbol_report(
        symbol="SPY",
        strategy_name="일회 매수",
        capital=1_000_000,
        start="2024-01-02",
        end="2024-01-04",
        generated_at_kst="2026-09-21 20:00:00",
        summary_row=summary_row,
        daily=result,
    )

    assert wb.sheetnames == ["요약", "일별 시계열"]

    summary_ws = wb["요약"]
    assert summary_ws["A1"].value == "SPY - 일회 매수 분석 결과"
    header_row = [cell.value for cell in summary_ws[6]]
    assert header_row == [
        "종목", "매수방식", "매수금액", "매수빈도", "이동평균조건", "등락구간", "익절선", "손절선",
        "총투자금", "실현손익", "평가손익", "합계", "수익률", "최대낙폭", "현금부족일수",
    ]
    assert summary_ws.cell(row=7, column=1).value == "SPY"
    assert summary_ws.cell(row=7, column=2).value == "일회 매수"
    assert summary_ws.cell(row=7, column=3).value == 1_000_000
    assert summary_ws.cell(row=7, column=13).value == 0.2  # 20% -> 0.2 (퍼센트 서식으로 표시)
    assert summary_ws.cell(row=7, column=13).number_format == "0.00%"
    assert summary_ws.cell(row=7, column=14).value == -0.05
    assert summary_ws.cell(row=7, column=15).value == 0

    daily_ws = wb["일별 시계열"]
    assert [cell.value for cell in daily_ws[1]][:6] == [
        "거래일", "종가", "당일매수금액", "당일매수부족금액", "당일매도금액", "매도유형",
    ]
    assert daily_ws.max_row == 1 + len(result)  # 헤더 한 줄 + 거래일 수만큼
    assert daily_ws.cell(row=2, column=1).value == "2024-01-02"
    # 첫날 일회 매수가 실행됐으니 당일매수금액(3번째 칸)이 찍혀야 한다
    assert daily_ws.cell(row=2, column=3).value == 1_000_000
    assert daily_ws.cell(row=2, column=4).value is None  # 현금이 모자라지 않았으니 부족금액은 빈칸
    assert daily_ws.cell(row=3, column=3).value is None  # 둘째 날은 안 샀다


def test_비교_보고서는_수익률_부호에_따라_글자색이_다르다():
    summary_rows = [
        {"symbol": "SPY", "strategy_key": "lump_sum", "strategy_name": "일회 매수",
         "매수방식": "일회 매수", "매수금액": 1_000_000,
         "총투자금": 1_000_000, "실현손익": 0, "평가손익": 200_000, "합계": 200_000, "수익률": 20.0,
         "현금부족일수": 0},
        {"symbol": "QQQ", "strategy_key": "lump_sum", "strategy_name": "일회 매수",
         "매수방식": "일회 매수", "매수금액": 1_000_000,
         "총투자금": 1_000_000, "실현손익": 0, "평가손익": -50_000, "합계": -50_000, "수익률": -5.0,
         "현금부족일수": 3},
    ]

    wb = build_comparison_report(
        symbols=["SPY", "QQQ"],
        capital=1_000_000,
        start="2024-01-01",
        end="2024-06-01",
        generated_at_kst="2026-09-21 20:00:00",
        summary_rows=summary_rows,
    )

    ws = wb["종목 비교"]
    assert ws["A1"].value == "종목 비교 결과 (SPY, QQQ)"
    header_row = [cell.value for cell in ws[6]]
    assert header_row == [
        "종목", "매수방식", "매수금액", "매수빈도", "이동평균조건", "등락구간", "익절선", "손절선",
        "총투자금", "실현손익", "평가손익", "합계", "수익률", "최대낙폭", "현금부족일수",
    ]
    positive_cell = ws.cell(row=7, column=13)
    negative_cell = ws.cell(row=8, column=13)
    assert positive_cell.value == 0.2
    assert negative_cell.value == -0.05
    assert positive_cell.font.color.rgb.endswith("1B7A43")
    assert negative_cell.font.color.rgb.endswith("B5502E")
    assert ws.cell(row=7, column=15).value == 0
    assert ws.cell(row=8, column=15).value == 3
    assert ws.cell(row=8, column=15).font.color.rgb.endswith("B5502E")  # 부족한 날이 있으면 강조


def test_포트폴리오_보고서는_요약과_일별_시계열_두_시트를_만든다():
    prices_a = _prices([100.0, 110.0, 120.0])
    prices_b = _prices([50.0, 55.0, 60.0])
    strategy_a = Strategy(key="lump_sum", name="A", buy_plan=LumpSum(500_000))
    strategy_b = Strategy(key="lump_sum", name="B", buy_plan=LumpSum(500_000))
    daily, breakdown = run_portfolio_backtest(
        {"A": prices_a, "B": prices_b}, capital=1_000_000, strategy_by_symbol={"A": strategy_a, "B": strategy_b}
    )
    summary = summarize_portfolio_result(daily, capital=1_000_000)

    wb = build_portfolio_report(
        symbols=["A", "B"],
        capital=1_000_000,
        start="2024-01-02",
        end="2024-01-04",
        generated_at_kst="2026-09-22 20:00:00",
        summary=summary,
        symbol_breakdown=breakdown,
        daily=daily,
    )

    assert wb.sheetnames == ["포트폴리오 요약", "일별 시계열"]
    ws = wb["포트폴리오 요약"]
    assert ws["A1"].value == "포트폴리오 백테스트 결과 (A, B)"
    header_row = [cell.value for cell in ws[7]]
    assert header_row == ["종목", "총투자금", "실현손익", "평가손익", "합계"]
    assert ws.cell(row=8, column=1).value == "A"
    assert ws.cell(row=8, column=2).value == 500_000
    assert ws.cell(row=10, column=1).value == "합계(포트폴리오)"
    assert ws.cell(row=10, column=2).value == summary["총투자금"]

    daily_ws = wb["일별 시계열"]
    assert daily_ws.max_row == 1 + len(daily)
    assert daily_ws.cell(row=2, column=2).value == 1_000_000  # 첫날 A+B 합쳐서 산 금액
