from datetime import date, timedelta

import pandas as pd
import pytest

from auto_trading.backtest import (
    STRATEGY_FACTORIES,
    ConditionalDCA,
    LumpSum,
    MovingAverageDCA,
    PeriodicDCA,
    Strategy,
    make_dca,
    make_drop_based,
    make_lump_sum,
    run_backtest,
)


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


def test_일회_매수는_첫날에만_산다():
    prices = _prices([100.0, 110.0, 120.0])
    strategy = Strategy(key="lump_sum", name="일회 매수", buy_plan=LumpSum(1_000_000))
    result = run_backtest(prices, capital=1_000_000, strategy=strategy)

    assert result.iloc[0]["invested_cumulative"] == 1_000_000
    assert result.iloc[1]["invested_cumulative"] == 1_000_000
    assert result.iloc[0]["shares"] == pytest.approx(10_000.0)


def test_적립식_매수는_간격마다_산다():
    prices = _prices([100.0] * 10)
    strategy = Strategy(key="dca", name="적립식", buy_plan=PeriodicDCA(amount=100_000, interval_days=3))
    result = run_backtest(prices, capital=1_000_000, strategy=strategy)

    # 0, 3, 6, 9일째에 산다 (4번)
    assert result.iloc[-1]["invested_cumulative"] == pytest.approx(400_000)


def test_이동평균_아래일_때만_산다():
    # 앞 60개는 100(이평선을 100으로 만들기 위한 예열), 그 뒤로 가격을 흔든다
    warmup = [100.0] * 60
    closes = warmup + [90.0, 110.0, 85.0]
    prices = _prices(closes)
    strategy = Strategy(
        key="dca_ma",
        name="이평 아래",
        buy_plan=MovingAverageDCA(amount=100_000, ma_window=60, interval_days=1, buy_when="below"),
    )
    result = run_backtest(prices, capital=10_000_000, strategy=strategy)

    # 60번째 인덱스(90.0, 이평 아래)에서는 사고, 61번째(110.0, 이평 위)에서는 안 산다
    assert result.iloc[60]["invested_cumulative"] > result.iloc[59]["invested_cumulative"]
    assert result.iloc[61]["invested_cumulative"] == result.iloc[60]["invested_cumulative"]


def test_하락폭이_클수록_많이_산다():
    # 21거래일 전 가격 100에서 각각 -3%, -5%, -8%로 떨어진 경우를 만든다
    base = [100.0] * 21
    prices = _prices(base + [97.0])  # -3%
    strategy = Strategy(
        key="drop",
        name="하락률",
        buy_plan=ConditionalDCA(tiers=[(-0.03, 100_000), (-0.05, 200_000), (-0.08, 300_000)], lookback_days=21, interval_days=1),
    )
    result = run_backtest(prices, capital=10_000_000, strategy=strategy)
    assert result.iloc[-1]["invested_cumulative"] == pytest.approx(100_000)

    prices8 = _prices(base + [91.0])  # -9%, 세 기준 모두 확실히 넘는다
    result8 = run_backtest(prices8, capital=10_000_000, strategy=strategy)
    assert result8.iloc[-1]["invested_cumulative"] == pytest.approx(300_000)


def test_떨어지지_않으면_안_산다():
    base = [100.0] * 21
    prices = _prices(base + [101.0])  # +1%, 어느 기준도 안 걸림
    strategy = Strategy(
        key="drop",
        name="하락률",
        buy_plan=ConditionalDCA(tiers=[(-0.03, 100_000)], lookback_days=21, interval_days=1),
    )
    result = run_backtest(prices, capital=10_000_000, strategy=strategy)
    assert result.iloc[-1]["invested_cumulative"] == 0.0


def test_목표_수익률에서_전량_매도한다():
    closes = [100.0, 100.0, 150.0, 150.0]  # 두 번째 날 +50%
    prices = _prices(closes)
    strategy = Strategy(key="lump_sum", name="일회", buy_plan=LumpSum(1_000_000), take_profit_pct=0.2)
    result = run_backtest(prices, capital=1_000_000, strategy=strategy)

    # 3번째 줄(인덱스 2)에서 +50% 도달, 매도돼서 보유 주식이 0이어야 한다
    assert result.iloc[2]["shares"] == 0.0
    assert result.iloc[2]["realized_pnl"] > 0


def test_매도_후에도_다음_매수가_이어진다():
    closes = [100.0, 150.0] + [150.0] * 3  # 하루 만에 +50% 익절
    prices = _prices(closes)
    strategy = Strategy(
        key="dca_tp", name="적립식+익절", buy_plan=PeriodicDCA(amount=200_000, interval_days=1), take_profit_pct=0.2
    )
    result = run_backtest(prices, capital=2_000_000, strategy=strategy)

    # 첫날 사고, 둘째날 익절(매도)하지만, 매도 판단은 그날 종가로 하고
    # 매수도 그날 다시 판단하므로 인덱스 1에서도 매수가 한 번 더 들어간다.
    assert result.iloc[1]["invested_cumulative"] > result.iloc[0]["invested_cumulative"]


def test_총자산은_항상_원금과_손익의_합이다():
    prices = _prices([100.0, 105.0, 95.0, 110.0, 90.0, 120.0])
    strategy = make_drop_based(1_000_000, periods=4, interval_days=1, lookback_days=1, take_profit_pct=0.1)
    result = run_backtest(prices, capital=1_000_000, strategy=strategy)

    for _, row in result.iterrows():
        assert row["total_value"] == pytest.approx(1_000_000 + row["total_pnl"], abs=1e-6)


def test_현금보다_많이_사지_않는다():
    prices = _prices([100.0] * 5)
    strategy = Strategy(key="lump_sum", name="일회", buy_plan=LumpSum(999_999_999))
    result = run_backtest(prices, capital=1_000_000, strategy=strategy)
    assert result.iloc[0]["invested_cumulative"] == 1_000_000
    assert result.iloc[0]["cash"] == 0.0


def test_전략_묶음이_다섯_개다():
    assert len(STRATEGY_FACTORIES) == 5


def test_레지스트리로_전략을_만들_수_있다():
    for factory in STRATEGY_FACTORIES.values():
        strategy = factory(1_000_000)
        assert strategy.key
        assert strategy.name


def test_적립식_전략_회차만큼_균등분할된다():
    strategy = make_dca(1_200_000, periods=12)
    assert strategy.buy_plan.amount == pytest.approx(100_000)


def test_일회매수_전략_이름이_있다():
    strategy = make_lump_sum(1_000_000)
    assert strategy.name == "일회 매수"
