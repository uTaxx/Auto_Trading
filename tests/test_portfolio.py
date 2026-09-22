from datetime import date, timedelta

import pandas as pd
import pytest

from auto_trading.backtest import LumpSum, PeriodicDCA, Strategy
from auto_trading.portfolio import run_portfolio_backtest, summarize_portfolio_result


def _prices(closes: list[float], start: date = date(2024, 1, 2)) -> pd.DataFrame:
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


def test_현금이_넉넉하면_종목별로_따로_계산한_것과_같다():
    prices_a = _prices([100.0, 110.0, 120.0])
    prices_b = _prices([50.0, 55.0, 60.0])
    strategy_a = Strategy(key="lump_sum", name="A", buy_plan=LumpSum(500_000))
    strategy_b = Strategy(key="lump_sum", name="B", buy_plan=LumpSum(500_000))

    daily, breakdown = run_portfolio_backtest(
        {"A": prices_a, "B": prices_b}, capital=2_000_000, strategy_by_symbol={"A": strategy_a, "B": strategy_b}
    )

    last = daily.iloc[-1]
    assert last["cash"] == 1_000_000
    assert last["invested_cumulative"] == 1_000_000
    assert last["unrealized_pnl"] == pytest.approx(200_000)  # A +100,000, B +100,000
    assert last["total_value"] == pytest.approx(2_200_000)
    assert breakdown["A"]["평가손익"] == pytest.approx(100_000)
    assert breakdown["B"]["평가손익"] == pytest.approx(100_000)


def test_현금이_모자라면_먼저_적힌_종목이_우선_쓴다():
    prices_a = _prices([10.0, 10.0])
    prices_b = _prices([10.0, 10.0])
    strategy_a = Strategy(key="dca", name="A", buy_plan=PeriodicDCA(amount=80.0))
    strategy_b = Strategy(key="dca", name="B", buy_plan=PeriodicDCA(amount=80.0))

    daily, breakdown = run_portfolio_backtest(
        {"A": prices_a, "B": prices_b}, capital=100.0, strategy_by_symbol={"A": strategy_a, "B": strategy_b}
    )

    day0 = daily.iloc[0]
    assert day0["buy_amount"] == 100.0  # A 80 + B 20(현금 부족)
    assert day0["buy_shortfall"] == 60.0  # B가 못 산 60
    assert day0["cash"] == 0.0

    day1 = daily.iloc[1]
    assert day1["buy_amount"] == 0.0  # 현금이 이미 0
    assert day1["buy_shortfall"] == 160.0  # A 80 + B 80

    assert breakdown["A"]["총투자금"] == 80.0
    assert breakdown["B"]["총투자금"] == 20.0


def test_같은_날_판_돈을_그날_다른_종목이_바로_쓴다():
    # A는 첫날 전액(100)을 사서 둘째 날 +15%에 전량 익절한다.
    # B는 첫날엔 현금이 없어 못 사고, 둘째 날 A가 판 돈(115)으로 산다.
    prices_a = _prices([100.0, 115.0])
    prices_b = _prices([50.0, 50.0])
    strategy_a = Strategy(key="lump_sum", name="A", buy_plan=LumpSum(100.0), take_profit_pct=0.1)
    strategy_b = Strategy(key="dca", name="B", buy_plan=PeriodicDCA(amount=115.0))

    daily, breakdown = run_portfolio_backtest(
        {"A": prices_a, "B": prices_b}, capital=100.0, strategy_by_symbol={"A": strategy_a, "B": strategy_b}
    )

    day0 = daily.iloc[0]
    assert day0["buy_amount"] == 100.0  # A만 산다
    assert day0["buy_shortfall"] == 115.0  # B가 사려던 115 전부 부족

    day1 = daily.iloc[1]
    assert day1["sell_amount"] == 115.0  # A 익절
    assert day1["buy_amount"] == 115.0  # 그 돈으로 B가 바로 산다
    assert day1["buy_shortfall"] == 0.0
    assert day1["cash"] == 0.0

    assert breakdown["A"]["실현손익"] == pytest.approx(15.0)
    assert breakdown["B"]["총투자금"] == 115.0


def test_종목마다_상장일이_달라도_계산된다():
    # A는 이틀치, B는 하루 늦게 시작해서 하루치만 있다.
    prices_a = _prices([100.0, 100.0], start=date(2024, 1, 2))
    prices_b = _prices([50.0], start=date(2024, 1, 3))
    strategy_a = Strategy(key="lump_sum", name="A", buy_plan=LumpSum(0.0))  # 안 사는 전략(계산만 확인)
    strategy_b = Strategy(key="dca", name="B", buy_plan=PeriodicDCA(amount=10.0))

    daily, breakdown = run_portfolio_backtest(
        {"A": prices_a, "B": prices_b}, capital=100.0, strategy_by_symbol={"A": strategy_a, "B": strategy_b}
    )

    assert len(daily) == 2  # 1/2, 1/3 합친 날짜
    assert daily.iloc[0]["trade_date"] == date(2024, 1, 2)
    assert daily.iloc[0]["buy_amount"] == 0.0  # B는 아직 상장 전이라 못 산다
    assert daily.iloc[1]["buy_amount"] == 10.0  # B가 상장일에 산다
    assert breakdown["B"]["총투자금"] == 10.0


def test_종목_구성이_다르면_오류():
    prices_a = _prices([100.0])
    strategy_a = Strategy(key="lump_sum", name="A", buy_plan=LumpSum(100.0))
    with pytest.raises(ValueError, match="종목 구성"):
        run_portfolio_backtest({"A": prices_a}, capital=100.0, strategy_by_symbol={"B": strategy_a})


def test_종목이_없으면_오류():
    with pytest.raises(ValueError, match="종목이 하나도"):
        run_portfolio_backtest({}, capital=100.0, strategy_by_symbol={})


def test_summarize_portfolio_result은_backtest_summarize_result와_같은_칸을_쓴다():
    prices_a = _prices([100.0, 110.0])
    prices_b = _prices([50.0, 55.0])
    strategy_a = Strategy(key="lump_sum", name="A", buy_plan=LumpSum(500_000))
    strategy_b = Strategy(key="lump_sum", name="B", buy_plan=LumpSum(500_000))

    daily, _ = run_portfolio_backtest(
        {"A": prices_a, "B": prices_b}, capital=1_000_000, strategy_by_symbol={"A": strategy_a, "B": strategy_b}
    )
    row = summarize_portfolio_result(daily, capital=1_000_000)

    assert set(row.keys()) == {
        "총투자금", "실현손익", "평가손익", "합계", "수익률", "최대낙폭", "현금부족일수", "현금부족금액",
    }
    assert row["총투자금"] == 1_000_000
    assert row["현금부족일수"] == 0
    assert row["수익률"] > 0
