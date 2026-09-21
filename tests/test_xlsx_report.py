from datetime import date, timedelta

import pandas as pd

from auto_trading.backtest import LumpSum, Strategy, run_backtest
from auto_trading.xlsx_report import build_comparison_report, build_symbol_report


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
        "총투자금": 1_000_000,
        "실현손익": 0,
        "평가손익": 200_000,
        "합계": 200_000,
        "수익률": 20.0,
        "최대낙폭": -5.0,
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
    assert header_row == ["종목", "전략", "총투자금", "실현손익", "평가손익", "합계", "수익률", "최대낙폭"]
    assert summary_ws.cell(row=7, column=1).value == "SPY"
    assert summary_ws.cell(row=7, column=7).value == 0.2  # 20% -> 0.2 (퍼센트 서식으로 표시)
    assert summary_ws.cell(row=7, column=7).number_format == "0.00%"
    assert summary_ws.cell(row=7, column=8).value == -0.05

    daily_ws = wb["일별 시계열"]
    assert [cell.value for cell in daily_ws[1]][:4] == ["거래일", "종가", "현금", "보유수량"]
    assert daily_ws.max_row == 1 + len(result)  # 헤더 한 줄 + 거래일 수만큼
    assert daily_ws.cell(row=2, column=1).value == "2024-01-02"


def test_비교_보고서는_수익률_부호에_따라_글자색이_다르다():
    summary_rows = [
        {"symbol": "SPY", "strategy_key": "lump_sum", "strategy_name": "일회 매수",
         "총투자금": 1_000_000, "실현손익": 0, "평가손익": 200_000, "합계": 200_000, "수익률": 20.0},
        {"symbol": "QQQ", "strategy_key": "lump_sum", "strategy_name": "일회 매수",
         "총투자금": 1_000_000, "실현손익": 0, "평가손익": -50_000, "합계": -50_000, "수익률": -5.0},
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
    positive_cell = ws.cell(row=7, column=7)
    negative_cell = ws.cell(row=8, column=7)
    assert positive_cell.value == 0.2
    assert negative_cell.value == -0.05
    assert positive_cell.font.color.rgb.endswith("1B7A43")
    assert negative_cell.font.color.rgb.endswith("B5502E")
