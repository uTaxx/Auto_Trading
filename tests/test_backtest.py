from datetime import date, timedelta

import pandas as pd
import pytest

from auto_trading.backtest import (
    STRATEGY_SCHEMAS,
    ConditionalDCA,
    LumpSum,
    MovingAverageDCA,
    PeriodicDCA,
    Strategy,
    build_strategy,
    describe_strategy,
    run_backtest,
    summarize_result,
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
        buy_plan=MovingAverageDCA(ma_window=60, below_amount=100_000, below_interval_days=1),
    )
    result = run_backtest(prices, capital=10_000_000, strategy=strategy)

    # 60번째 인덱스(90.0, 이평 아래)에서는 사고, 61번째(110.0, 이평 위)에서는 안 산다
    assert result.iloc[60]["invested_cumulative"] > result.iloc[59]["invested_cumulative"]
    assert result.iloc[61]["invested_cumulative"] == result.iloc[60]["invested_cumulative"]


def test_이동평균_위일_때만_산다():
    warmup = [100.0] * 60
    closes = warmup + [90.0, 110.0, 85.0]
    prices = _prices(closes)
    strategy = Strategy(
        key="dca_ma",
        name="이평 위",
        buy_plan=MovingAverageDCA(ma_window=60, above_amount=50_000, above_interval_days=1),
    )
    result = run_backtest(prices, capital=10_000_000, strategy=strategy)

    # 60번째(90.0, 이평 아래)에서는 안 사고, 61번째(110.0, 이평 위)에서는 산다
    assert result.iloc[60]["invested_cumulative"] == result.iloc[59]["invested_cumulative"]
    assert result.iloc[61]["invested_cumulative"] > result.iloc[60]["invested_cumulative"]


def test_이동평균_아래_위를_다른_금액으로_동시에_살_수_있다():
    warmup = [100.0] * 60
    closes = warmup + [90.0, 110.0]
    prices = _prices(closes)
    strategy = Strategy(
        key="dca_ma",
        name="이평 아래위",
        buy_plan=MovingAverageDCA(
            ma_window=60,
            below_amount=100_000,
            below_interval_days=1,
            above_amount=50_000,
            above_interval_days=1,
        ),
    )
    result = run_backtest(prices, capital=10_000_000, strategy=strategy)

    below_invested = result.iloc[60]["invested_cumulative"] - result.iloc[59]["invested_cumulative"]
    above_invested = result.iloc[61]["invested_cumulative"] - result.iloc[60]["invested_cumulative"]
    assert below_invested == pytest.approx(100_000)
    assert above_invested == pytest.approx(50_000)


def test_하락폭이_클수록_많이_산다():
    # 21거래일 전 가격 100에서 각각 -3%, -9%로 떨어진 경우를 만든다
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
    strategy = build_strategy(
        {"key": "drop_based", "interval_days": 1, "lookback_days": 1, "tiers": [[-3, 100_000]], "take_profit_pct": 0.1},
        capital=1_000_000,
    )
    result = run_backtest(prices, capital=1_000_000, strategy=strategy)

    for _, row in result.iterrows():
        assert row["total_value"] == pytest.approx(1_000_000 + row["total_pnl"], abs=1e-6)


def test_현금보다_많이_사지_않는다():
    prices = _prices([100.0] * 5)
    strategy = Strategy(key="lump_sum", name="일회", buy_plan=LumpSum(999_999_999))
    result = run_backtest(prices, capital=1_000_000, strategy=strategy)
    assert result.iloc[0]["invested_cumulative"] == 1_000_000
    assert result.iloc[0]["cash"] == 0.0


def test_전략_종류가_네_가지다():
    assert set(STRATEGY_SCHEMAS.keys()) == {"lump_sum", "dca", "dca_ma", "drop_based"}


def test_일회매수는_추가_입력값이_없어도_만들어진다():
    strategy = build_strategy({"key": "lump_sum"}, capital=1_000_000)
    assert strategy.buy_plan.amount == 1_000_000


def test_적립식_매수는_필요한_값을_안_주면_오류를_낸다():
    with pytest.raises(ValueError, match="amount"):
        build_strategy({"key": "dca", "interval_days": 21}, capital=1_000_000)


def test_이동평균_전략은_필요한_값을_안_주면_오류를_낸다():
    with pytest.raises(ValueError, match="ma_window"):
        build_strategy({"key": "dca_ma", "below_amount": 100_000, "below_interval_days": 21}, capital=1_000_000)


def test_이동평균_전략은_아래_위_둘_다_안_주면_오류를_낸다():
    with pytest.raises(ValueError, match="최소 한쪽"):
        build_strategy({"key": "dca_ma", "ma_window": 60}, capital=1_000_000)


def test_이동평균_전략은_금액만_주고_빈도를_안_주면_오류를_낸다():
    with pytest.raises(ValueError, match="같이 넣어야"):
        build_strategy({"key": "dca_ma", "ma_window": 60, "below_amount": 100_000}, capital=1_000_000)


def test_이동평균_전략은_아래_위를_따로_넣을_수_있다():
    strategy = build_strategy(
        {
            "key": "dca_ma",
            "ma_window": 60,
            "below_amount": 100_000,
            "below_interval_days": 1,
            "above_amount": 50_000,
            "above_interval_days": 5,
        },
        capital=1_000_000,
    )
    assert strategy.buy_plan.below_amount == 100_000
    assert strategy.buy_plan.below_interval_days == 1
    assert strategy.buy_plan.above_amount == 50_000
    assert strategy.buy_plan.above_interval_days == 5


def test_하락률_전략은_구간을_안_주면_오류를_낸다():
    with pytest.raises(ValueError, match="tiers"):
        build_strategy({"key": "drop_based", "interval_days": 21, "lookback_days": 20}, capital=1_000_000)


def test_하락률_전략의_퍼센트_입력은_비율로_바뀐다():
    strategy = build_strategy(
        {"key": "drop_based", "interval_days": 21, "lookback_days": 20, "tiers": [[-3, 100_000]]},
        capital=1_000_000,
    )
    assert strategy.buy_plan.tiers == [(-0.03, 100_000.0)]


def test_모르는_전략_키는_오류를_낸다():
    with pytest.raises(ValueError, match="모르는 전략 키"):
        build_strategy({"key": "no_such_strategy"}, capital=1_000_000)


def test_적립식_매수_금액은_입력한_값을_그대로_쓴다():
    strategy = build_strategy({"key": "dca", "amount": 300_000, "interval_days": 21}, capital=1_200_000)
    assert strategy.buy_plan.amount == 300_000


def test_손절선에서_전량_매도한다():
    closes = [100.0, 100.0, 80.0, 80.0]  # 두 번째 날 -20%
    prices = _prices(closes)
    strategy = Strategy(key="lump_sum", name="일회", buy_plan=LumpSum(1_000_000), stop_loss_pct=0.1)
    result = run_backtest(prices, capital=1_000_000, strategy=strategy)

    # 3번째 줄(인덱스 2)에서 -20% 도달, 손절선(-10%)을 넘어서 매도된다
    assert result.iloc[2]["shares"] == 0.0
    assert result.iloc[2]["realized_pnl"] < 0


def test_손절선을_안_주면_안_판다():
    closes = [100.0, 100.0, 50.0]  # -50%까지 떨어져도
    prices = _prices(closes)
    strategy = Strategy(key="lump_sum", name="일회", buy_plan=LumpSum(1_000_000))
    result = run_backtest(prices, capital=1_000_000, strategy=strategy)
    assert result.iloc[-1]["shares"] > 0.0


def test_상승_구간에도_매수_비중을_넣을_수_있다():
    base = [100.0] * 21
    prices = _prices(base + [105.0])  # +5%
    strategy = Strategy(
        key="drop",
        name="등락률",
        buy_plan=ConditionalDCA(tiers=[(0.05, 150_000)], lookback_days=21, interval_days=1),
    )
    result = run_backtest(prices, capital=10_000_000, strategy=strategy)
    assert result.iloc[-1]["invested_cumulative"] == pytest.approx(150_000)


def test_전략_설명은_매수방식별로_읽을_수_있는_칸을_만든다():
    lump_sum = describe_strategy(Strategy(key="lump_sum", name="일회", buy_plan=LumpSum(1_000_000)))
    assert lump_sum["매수방식"] == "일회 매수"
    assert lump_sum["매수금액"] == 1_000_000
    assert lump_sum["매수빈도"] is None

    dca = describe_strategy(
        Strategy(key="dca", name="적립", buy_plan=PeriodicDCA(amount=100_000, interval_days=5))
    )
    assert dca["매수금액"] == 100_000
    assert dca["매수빈도"] == 5

    dca_ma = describe_strategy(
        Strategy(
            key="dca_ma",
            name="이평",
            buy_plan=MovingAverageDCA(ma_window=60, below_amount=100_000, below_interval_days=1),
            take_profit_pct=0.1,
        )
    )
    assert "60일선" in dca_ma["이동평균조건"]
    assert "아래" in dca_ma["이동평균조건"]
    assert "위" not in dca_ma["이동평균조건"]
    assert dca_ma["익절선"] == "10.0%"
    assert dca_ma["손절선"] is None

    drop_based = describe_strategy(
        Strategy(
            key="drop_based",
            name="등락",
            buy_plan=ConditionalDCA(tiers=[(-0.05, 120_000)], lookback_days=1, interval_days=1),
        )
    )
    assert "-5.0%" in drop_based["등락구간"]
    assert "120,000원" in drop_based["등락구간"]


def test_요약에_전략_설명_칸이_같이_들어간다():
    prices = _prices([100.0, 105.0, 110.0])
    strategy = Strategy(key="dca", name="적립", buy_plan=PeriodicDCA(amount=100_000, interval_days=1))
    result = run_backtest(prices, capital=1_000_000, strategy=strategy)
    row = summarize_result("SPY", strategy, 1_000_000, result)
    assert row["매수방식"] == "적립식 매수"
    assert row["매수금액"] == 100_000
    assert row["매수빈도"] == 1
