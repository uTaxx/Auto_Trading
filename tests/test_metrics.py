from datetime import date, timedelta

import pandas as pd
import pytest

from auto_trading.metrics import (
    cagr_pct,
    calmar_ratio,
    extended_metrics,
    mdd_recovery_days,
    trade_count,
)


def _daily(rows: list[tuple[str, float, float, float]]) -> pd.DataFrame:
    """rows: (날짜문자열, total_value, buy_amount, sell_amount)"""
    return pd.DataFrame(
        {
            "trade_date": [date.fromisoformat(r[0]) for r in rows],
            "total_value": [r[1] for r in rows],
            "buy_amount": [r[2] for r in rows],
            "sell_amount": [r[3] for r in rows],
        }
    )


def test_CAGR은_1년_뒤_2배면_100퍼센트에_가깝다():
    daily = _daily([("2020-01-01", 1_000_000, 0, 0), ("2021-01-01", 2_000_000, 0, 0)])
    value = cagr_pct(daily["total_value"], daily["trade_date"], capital=1_000_000)
    assert value == pytest.approx(99.93, abs=0.5)


def test_CAGR은_기간이_0이면_None():
    daily = _daily([("2020-01-01", 1_000_000, 0, 0)])
    assert cagr_pct(daily["total_value"], daily["trade_date"], capital=1_000_000) is None


def test_최대낙폭_회복일수_회복한_경우():
    daily = _daily(
        [
            ("2020-01-01", 1_000_000, 0, 0),
            ("2020-01-02", 800_000, 0, 0),  # 여기서 -20% 낙폭
            ("2020-01-03", 900_000, 0, 0),
            ("2020-01-10", 1_100_000, 0, 0),  # 여기서 처음 최고점(100만) 재돌파
        ]
    )
    days = mdd_recovery_days(daily["total_value"], daily["trade_date"])
    assert days == 8  # 1/2 ~ 1/10


def test_최대낙폭_회복일수_구간_안에서_회복_못하면_None():
    daily = _daily([("2020-01-01", 1_000_000, 0, 0), ("2020-01-02", 800_000, 0, 0)])
    assert mdd_recovery_days(daily["total_value"], daily["trade_date"]) is None


def test_최대낙폭_회복일수_안_빠졌으면_0():
    daily = _daily([("2020-01-01", 1_000_000, 0, 0), ("2020-01-02", 1_100_000, 0, 0)])
    assert mdd_recovery_days(daily["total_value"], daily["trade_date"]) == 0


def test_Calmar은_CAGR_나누기_최대낙폭_절댓값():
    assert calmar_ratio(20.0, -10.0) == pytest.approx(2.0)


def test_Calmar은_낙폭_0이면_None():
    assert calmar_ratio(20.0, 0.0) is None
    assert calmar_ratio(None, -10.0) is None


def test_거래횟수는_매수와_매도를_합친다():
    daily = _daily(
        [
            ("2020-01-01", 1_000_000, 100_000, 0),
            ("2020-01-02", 1_000_000, 0, 0),
            ("2020-01-03", 1_000_000, 0, 200_000),
            ("2020-01-04", 1_000_000, 50_000, 0),
        ]
    )
    assert trade_count(daily) == 3


def test_extended_metrics_빈_구간은_None들():
    empty = pd.DataFrame({"trade_date": [], "total_value": [], "buy_amount": [], "sell_amount": []})
    result = extended_metrics(empty, capital=1_000_000)
    assert result["누적수익률"] is None
    assert result["거래횟수"] == 0


def test_extended_metrics_전부_계산된다():
    start = date(2020, 1, 1)
    rows = []
    value = 1_000_000
    for i in range(400):
        d = start + timedelta(days=i)
        buy = 10_000 if i % 10 == 0 else 0
        value = value * 1.0005
        rows.append((d.isoformat(), value, buy, 0))
    daily = _daily(rows)
    result = extended_metrics(daily, capital=1_000_000)
    assert result["누적수익률"] > 0
    assert result["CAGR"] is not None
    assert result["최대낙폭"] <= 0
    assert result["거래횟수"] == 40
